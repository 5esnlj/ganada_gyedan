from collections.abc import Mapping

from app.core.access import active_student_membership, membership
from app.core.errors import fail
from app.core.state import MemoryStore, Record, timestamp

from .dto import (
    DiagnosticAnswer,
    DiagnosticAnswerOut,
    DiagnosticListen,
    DiagnosticListenOut,
    DiagnosticNext,
    DiagnosticQuestion,
    DiagnosticResults,
    DiagnosticStart,
    DiagnosticStateCode,
    DiagnosticStatus,
    GradeBand,
    StudentRow,
)
from .forms import DiagnosticForm, DiagnosticItem, FormProvider
from .grading import grade


LISTENING_PLAY_LIMIT = 2


def new_diagnostic(form: DiagnosticForm, class_id: str) -> Record:
    return {
        "form": form,
        "originClassId": class_id,
        "status": "in_progress",
        "startedAt": timestamp(),
        "completedAt": None,
        "answers": {},
        "listeningPlays": {},
        "results": None,
    }


def diagnostic_state(state: MemoryStore, student_id: str) -> DiagnosticStateCode:
    diagnostic = state.diagnostics.get(student_id)
    return diagnostic["status"] if diagnostic is not None else "not_started"


def completed_results(state: MemoryStore, student_id: str) -> DiagnosticResults | None:
    diagnostic = state.diagnostics.get(student_id)
    if diagnostic is not None and diagnostic["status"] == "completed":
        return diagnostic["results"]
    return None


def pending_question(form: DiagnosticForm, answers: dict[str, str | None]) -> DiagnosticItem | None:
    return min(
        (item for item in form.questions if item.display.id not in answers),
        key=lambda item: item.display.sequence,
        default=None,
    )


def progress(
    diagnostic: Record | None, active_form: DiagnosticForm | None = None
) -> DiagnosticStatus:
    if diagnostic is None:
        pending = pending_question(active_form, {}) if active_form else None
        return DiagnosticStatus(
            status="not_started", formId=None,
            totalQuestions=len(active_form.questions) if active_form else 0,
            completedQuestions=0, nextQuestionId=pending.display.id if pending else None,
        )

    form = diagnostic["form"]
    answers = diagnostic["answers"]
    pending = pending_question(form, answers)
    grade_fields = {"gradeBand": form.grade_band} if form.grade_band is not None else {}
    return DiagnosticStatus(
        status=diagnostic["status"], formId=form.id, totalQuestions=len(form.questions),
        completedQuestions=len(answers), nextQuestionId=pending.display.id if pending else None,
        **grade_fields,
    )


def can_select_grade_band(diagnostic: Record | None) -> bool:
    return diagnostic is None or (
        diagnostic.get("demoSeed", False)
        and diagnostic["status"] == "in_progress"
        and not diagnostic["answers"]
    )


def start(
    state: MemoryStore,
    user: Record,
    request: DiagnosticStart,
    provider: FormProvider,
    assessment_forms: Mapping[GradeBand, DiagnosticForm],
) -> DiagnosticStatus:
    membership(state, user, request.classId, "student")
    diagnostic = state.diagnostics.get(user["id"])
    if diagnostic is None or (request.gradeBand is not None and can_select_grade_band(diagnostic)):
        form = (
            assessment_forms.get(request.gradeBand)
            if request.gradeBand is not None
            else provider.active_form()
        )
        if form is None:
            fail(503, "DIAGNOSTIC_FORM_UNAVAILABLE", "공개된 진단 문항집이 없습니다.")
        diagnostic = new_diagnostic(form, request.classId)
        state.diagnostics[user["id"]] = diagnostic
    return progress(diagnostic)


def active_diagnostic(state: MemoryStore, user: Record) -> Record:
    has_class = any(
        member["userId"] == user["id"]
        and active_student_membership(state, member["id"], member["classId"]) is not None
        for member in state.memberships.values()
    )
    if not has_class:
        fail(403, "CLASS_REQUIRED", "먼저 클래스 초대를 수락해 주세요.")
    diagnostic = state.diagnostics.get(user["id"])
    if diagnostic is None:
        fail(409, "DIAGNOSTIC_NOT_STARTED", "진단을 시작해 주세요.")
    return diagnostic


def next_question(diagnostic: Record) -> DiagnosticNext:
    form = diagnostic["form"]
    item = pending_question(form, diagnostic["answers"])
    question = None
    if item is not None:
        display = item.display.model_dump(exclude_unset=True)
        if form.grade_band is not None:
            section = [
                candidate.display for candidate in form.questions
                if candidate.display.skill == item.display.skill
            ]
            display.update(
                maxPoints=float(item.max_points),
                sectionNumber=sum(candidate.sequence <= item.display.sequence for candidate in section),
                sectionTotal=len(section),
            )
        if item.display.skill == "listening" and item.display.audioUrl:
            plays = (diagnostic["listeningPlays"] or {}).get(item.display.id, [])
            display["listeningPlaysRemaining"] = LISTENING_PLAY_LIMIT - len(plays)
        question = DiagnosticQuestion(**display, isSynthetic=form.is_synthetic)
    return DiagnosticNext(progress=progress(diagnostic), question=question)


def register_listening_play(diagnostic: Record, body: DiagnosticListen) -> DiagnosticListenOut:
    item = pending_question(diagnostic["form"], diagnostic["answers"])
    if item is None or body.questionId != item.display.id:
        fail(409, "QUESTION_OUT_OF_ORDER", "현재 듣기 문항만 재생할 수 있습니다.")
    if item.display.skill != "listening" or not item.display.audioUrl:
        fail(422, "DIAGNOSTIC_AUDIO_UNAVAILABLE", "이 문항에는 재생할 음원이 없습니다.")

    stored = diagnostic["listeningPlays"]
    if stored is None:
        fail(503, "DATABASE_MIGRATION_REQUIRED", "듣기 횟수를 저장하려면 MariaDB 업데이트가 필요합니다.")
    plays = stored.get(body.questionId, [])
    if body.playId not in plays:
        if len(plays) >= LISTENING_PLAY_LIMIT:
            fail(409, "DIAGNOSTIC_LISTENING_LIMIT", "이 문항의 듣기 횟수를 모두 사용했습니다.")
        plays = [*plays, body.playId]
        diagnostic["listeningPlays"] = {**stored, body.questionId: plays}
    return DiagnosticListenOut(remainingPlays=LISTENING_PLAY_LIMIT - len(plays))


def submit_answer(diagnostic: Record, answer: DiagnosticAnswer) -> DiagnosticAnswerOut:
    answers = diagnostic["answers"]
    if answer.questionId in answers:
        if answers[answer.questionId] != answer.selectedOptionId:
            fail(409, "ANSWER_IMMUTABLE", "제출한 진단 답안은 바꿀 수 없습니다.")
        return DiagnosticAnswerOut(progress=progress(diagnostic), replayed=True)
    if diagnostic["status"] == "completed":
        fail(409, "DIAGNOSTIC_COMPLETED", "이미 완료한 진단입니다.")

    form = diagnostic["form"]
    item = pending_question(form, answers)
    if item is None or answer.questionId != item.display.id:
        fail(409, "QUESTION_OUT_OF_ORDER", "다음 미제출 문제부터 답해 주세요.")
    if answer.selectedOptionId is not None and all(
        answer.selectedOptionId != option.id for option in item.display.options
    ):
        fail(422, "OPTION_INVALID", "문제의 보기를 선택해 주세요.")

    updated_answers = {**answers, answer.questionId: answer.selectedOptionId}
    if len(updated_answers) == len(form.questions):
        # Grade before mutating so the last answer and results are committed together.
        results = grade(form, updated_answers)
        diagnostic.update(
            answers=updated_answers, results=results, status="completed", completedAt=timestamp()
        )
    else:
        diagnostic["answers"] = updated_answers
    return DiagnosticAnswerOut(progress=progress(diagnostic), replayed=False)


def class_students(state: MemoryStore, class_id: str) -> list[StudentRow]:
    roster = [
        member
        for member in state.memberships.values()
        if active_student_membership(state, member["id"], class_id) is not None
    ]
    roster.sort(key=lambda member: (member.get("rosterNumber") or "", member["userId"]))
    items = []
    for member in roster:
        student = state.users[member["userId"]]
        result = completed_results(state, student["id"])
        items.append(StudentRow(
            id=member["id"], userId=student["id"], displayName=student["displayName"],
            nativeLanguage=student["nativeLanguage"],
            diagnosticStatus=diagnostic_state(state, student["id"]),
            partResults=result.parts if result else [],
            areaResults=result.areas if result else [],
        ))
    return items
