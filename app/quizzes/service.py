import json
from hashlib import sha256
from math import fsum

from pydantic import ValidationError

from app.core.access import (
    active_student_membership,
    membership,
    require_active_teacher,
    require_resource,
    visible_resource,
)
from app.core.dto import Area, Kind
from app.core.errors import fail
from app.core.review import bump_version, transition
from app.core.state import MemoryStore, Record, new_id, timestamp
from app.diagnostic.service import completed_results

from .dto import (
    AnswerOut,
    Passage,
    QuestionPatch,
    QuizAnswer,
    QuizDraft,
    QuizSummary,
    ScoreResults,
    StudentQuestion,
    StudentQuiz,
    TeacherQuiz,
    WrongQuestion,
)
from .generation import adjustment_for, first_attempts, quiz_history

SUBJECT_ORDER = ("korean", "math", "social", "science", "daily")


def _matches_input(quiz: Record, material: Record | None) -> bool:
    return bool(
        material
        and all(material[key] == quiz[key] for key in ("classId", "sourceId", "studentMemberId"))
    )


def _valid_input(state: MemoryStore, quiz: Record, user: Record) -> bool:
    material_id = quiz.get("inputMaterialId")
    if material_id is None:
        return True
    material = state.materials.get(material_id)
    return _matches_input(quiz, material) and (
        user["role"] == "teacher" or material["status"] == "published"
    )


def quiz_access(state: MemoryStore, user: Record, quiz_id: str, write: bool = False) -> Record:
    quiz = state.quizzes.get(quiz_id)
    if quiz is None or not _valid_input(state, quiz, user):
        fail(404, "NOT_FOUND", "문제 묶음을 찾을 수 없습니다.")
    return require_resource(state, user, quiz, write=write)


def visible_quizzes(
    state: MemoryStore, user: Record, class_id: str, subject: Area | None = None
) -> list[Record]:
    membership(state, user, class_id)
    quizzes = [
        quiz
        for quiz in state.quizzes.values()
        if quiz["classId"] == class_id
        and (subject is None or quiz["subject"] == subject)
        and _valid_input(state, quiz, user)
        and visible_resource(state, user, quiz)
    ]
    if user["role"] == "teacher":
        return sorted(quizzes, key=lambda quiz: (quiz["createdAt"], quiz["id"]), reverse=True)
    quizzes.sort(key=lambda quiz: (quiz["publishedAt"], quiz["id"]), reverse=True)
    quizzes.sort(key=lambda quiz: SUBJECT_ORDER.index(quiz["subject"]))
    return quizzes


def _passage(state: MemoryStore, quiz: Record) -> Passage:
    input_id = quiz.get("inputMaterialId")
    if input_id:
        content = state.materials[input_id]["content"]
        hidden = set(content.get("hiddenBlockIds", []))
        paragraphs = [row for row in content["paragraphs"] if row["id"] not in hidden]
        return Passage(paragraphs=paragraphs, translations=content.get("translations", []))
    sentences = state.sources[quiz["sourceId"]]["sentences"]
    if quiz.get("approvedSentenceIds") is not None:
        approved = set(quiz["approvedSentenceIds"])
        sentences = [row for row in sentences if row["id"] in approved]
    return Passage(paragraphs=sentences)


def quiz_results(state: MemoryStore, quiz: Record) -> ScoreResults:
    attempts = state.attempts.get(quiz["id"], {})
    questions = quiz["questions"]
    retry_counts = {}
    first, latest, wrong = [], [], []
    for question in questions:
        question_id = question["id"]
        answers = attempts.get(question_id, [])
        retry_counts[question_id] = max(0, len(answers) - 1)
        if not answers:
            continue
        first.append(answers[0])
        latest.append(answers[-1])
        if not answers[-1]["isCorrect"]:
            wrong.append(
                WrongQuestion(
                    questionId=question_id,
                    position=question["position"],
                    retryCount=retry_counts[question_id],
                )
            )

    # Published questions and first attempts are immutable, so the first score can be derived.
    completed = bool(questions) and len(first) == len(questions)
    status = "completed" if completed else "in_progress" if first else "not_started"
    return ScoreResults(
        quizId=quiz["id"],
        questionCount=len(questions),
        submittedQuestionCount=len(first),
        status=status,
        firstScore=fsum(answer["points"] for answer in first) if completed else None,
        maxScore=fsum(question["maxPoints"] for question in questions),
        firstCorrectCount=sum(answer["isCorrect"] for answer in first) if completed else None,
        latestCorrectCount=sum(answer["isCorrect"] for answer in latest),
        currentWrongCount=len(wrong),
        unansweredQuestionCount=len(questions) - len(first),
        retryCounts=retry_counts,
        wrongQuestions=wrong,
        answeredQuestionIds=[q["id"] for q in questions if attempts.get(q["id"])],
    )


def last_answered_at(state: MemoryStore, quiz_id: str) -> str | None:
    return max(
        (
            answer["answeredAt"]
            for attempts in state.attempts.get(quiz_id, {}).values()
            for answer in attempts
        ),
        default=None,
    )


def quiz_output(state: MemoryStore, quiz: Record, user: Record) -> StudentQuiz | TeacherQuiz:
    # Construct the student DTO from an allowlist. A teacher payload is never reused.
    question_count = len(quiz["questions"])
    if user["role"] == "teacher":
        firsts = first_attempts(state, quiz)
        generation = quiz.get("generation")
        if generation:
            generation = generation | {"nextAdjustment": adjustment_for(quiz_history(state, quiz))}
        return TeacherQuiz.model_validate({
            **quiz,
            "questionCount": question_count,
            "questions": [q | {"firstAttemptCorrect": first} for q, first in zip(quiz["questions"], firsts)],
            "generation": generation,
            "progress": quiz_results(state, quiz),
            "passage": _passage(state, quiz),
        })
    summary = QuizSummary.model_validate({**quiz, "questionCount": question_count})
    return StudentQuiz(
        **summary.model_dump(),
        questions=[StudentQuestion.model_validate(question) for question in quiz["questions"]],
        progress=quiz_results(state, quiz),
        passage=_passage(state, quiz),
    )


def get_question(quiz: Record, question_id: str) -> Record:
    question = next((item for item in quiz["questions"] if item["id"] == question_id), None)
    if question is None:
        fail(404, "NOT_FOUND", "문항을 찾을 수 없습니다.")
    return question


def _validate_question(body: QuestionPatch, kind: Kind, sentence_ids: set[str]) -> None:
    choice_ids = {option.id for option in body.choices}
    expected_count = 2 if kind == "true_false" else 4
    if (
        len(body.choices) != expected_count
        or len(choice_ids) != expected_count
        or len({option.text.strip() for option in body.choices}) != expected_count
        or body.correctOptionId not in choice_ids
        or not sentence_ids.issuperset(body.sourceSentenceIds)
        or len(set(body.sourceSentenceIds)) != len(body.sourceSentenceIds)
        or not body.prompt.strip()
        or not body.explanation.strip()
        or any(not option.id.strip() or not option.text.strip() for option in body.choices)
    ):
        fail(422, "QUESTION_INVALID", "보기 수, 정답, 지문 출처와 문항 내용을 확인하세요.")


def create_quiz_draft(state: MemoryStore, teacher: Record, body: QuizDraft) -> Record:
    """Import generated content at the trusted application boundary inside a unit of work."""
    teacher = require_active_teacher(state, teacher["id"])
    source = state.sources.get(body.sourceId)
    if not source or source["archived"]:
        fail(404, "NOT_FOUND", "원본 자료를 찾을 수 없습니다.")
    member = membership(state, teacher, source["classId"], "teacher")
    if member["teacherSubject"] != source["subject"]:
        fail(404, "NOT_FOUND", "원본 자료를 찾을 수 없습니다.")
    student = active_student_membership(state, body.studentMemberId, source["classId"])
    if student is None:
        fail(404, "NOT_FOUND", "대상 학생을 찾을 수 없습니다.")
    if completed_results(state, student["userId"]) is None:
        fail(409, "STUDENT_DIAGNOSTIC_REQUIRED", "대상 학생의 최초 진단을 먼저 완료해야 합니다.")

    if not body.title.strip():
        fail(422, "QUESTION_INVALID", "문제 묶음의 제목을 입력하세요.")

    quiz = {
        "classId": source["classId"],
        "sourceId": body.sourceId,
        "studentMemberId": body.studentMemberId,
        "inputMaterialId": body.inputMaterialId,
        "approvedSentenceIds": body.approvedSentenceIds,
        "subject": source["subject"],
    }
    if body.inputMaterialId is not None:
        material = state.materials.get(body.inputMaterialId)
        if not _matches_input(quiz, material):
            fail(404, "NOT_FOUND", "입력 전환본을 찾을 수 없습니다.")
        if material["status"] != "published":
            fail(409, "INPUT_MATERIAL_UNPUBLISHED", "입력 전환본을 먼저 배포하세요.")

    passage = _passage(state, quiz)
    sentence_ids = {paragraph.id for paragraph in passage.paragraphs}
    if body.approvedSentenceIds is not None and set(body.approvedSentenceIds) != sentence_ids:
        fail(422, "QUESTION_INVALID", "원본 지문의 문장 선택을 다시 확인하세요.")
    for question in body.questions:
        _validate_question(question, body.questionType, sentence_ids)
    max_generation = max(
        (
            existing["generationNo"]
            for existing in state.quizzes.values()
            if existing["sourceId"] == body.sourceId
            and existing["studentMemberId"] == body.studentMemberId
        ),
        default=0,
    )
    quiz_id = new_id("quiz")
    now = timestamp()
    passage_text = (
        json.dumps(
            state.materials[body.inputMaterialId]["content"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if body.inputMaterialId
        else "\n".join(p.text for p in passage.paragraphs) if body.approvedSentenceIds is not None
        else source["sourceText"]
    )
    quiz.update(
        id=quiz_id,
        title=body.title,
        questionType=body.questionType,
        status="draft",
        version=1,
        generationNo=max_generation + 1,
        createdBy=teacher["id"],
        createdAt=now,
        updatedAt=now,
        reviewedAt=None,
        publishedAt=None,
        sourceHash=sha256(passage_text.encode()).hexdigest(),
        questions=[
            {
                **question.model_dump(),
                "id": f"{quiz_id}_q{index}",
                "position": index,
                "questionType": body.questionType,
            }
            for index, question in enumerate(body.questions, start=1)
        ],
    )
    state.quizzes[quiz_id] = quiz
    return quiz


def edit_question(
    state: MemoryStore, quiz: Record, question_id: str, body: QuestionPatch
) -> None:
    if quiz["status"] not in {"draft", "reviewed"} or quiz["publishedAt"]:
        fail(409, "PUBLISHED_IMMUTABLE", "배포 문제는 새 묶음으로 만드세요.")
    question = get_question(quiz, question_id)
    _validate_question(body, quiz["questionType"], {p.id for p in _passage(state, quiz).paragraphs})
    question.update(body.model_dump())
    quiz.update(status="draft", reviewedAt=None, reviewedBy=None)
    bump_version(quiz)


def transition_quiz(state: MemoryStore, quiz: Record, actor: Record, target: str) -> None:
    if not active_student_membership(state, quiz["studentMemberId"], quiz["classId"]):
        fail(404, "NOT_FOUND", "대상 학생을 찾을 수 없습니다.")
    input_id = quiz.get("inputMaterialId")
    if input_id and state.materials[input_id]["status"] != "published":
        fail(409, "INPUT_MATERIAL_UNPUBLISHED", "입력 전환본을 먼저 배포하세요.")
    if target == "reviewed" and quiz["status"] == "draft":
        if not quiz["questions"]:
            fail(422, "QUESTION_INVALID", "검토할 문항이 없습니다.")
        sentence_ids = {p.id for p in _passage(state, quiz).paragraphs}
        for question in quiz["questions"]:
            try:
                fields = {key: question[key] for key in QuestionPatch.model_fields}
                body = QuestionPatch.model_validate(fields)
            except (KeyError, ValidationError):
                fail(422, "QUESTION_INVALID", "검토할 문항 내용을 확인하세요.")
            _validate_question(body, quiz["questionType"], sentence_ids)
    transition(quiz, target)
    quiz["reviewedBy" if target == "reviewed" else "publishedBy"] = actor["id"]


def submit_answer(
    state: MemoryStore, quiz: Record, user: Record, body: QuizAnswer, request_key: str | None
) -> AnswerOut:
    if not request_key or not request_key.strip() or len(request_key) > 64 or not request_key.isascii():
        fail(400, "IDEMPOTENCY_KEY_REQUIRED", "1~64자 Idempotency-Key가 필요합니다.")
    quiz_id = quiz["id"]
    key = (user["id"], f"answer:{quiz_id}", request_key)
    payload = (body.questionId, body.selectedOptionId)
    previous = state.idempotency.get(key)
    if previous is not None:
        if previous["payload"] != payload:
            fail(409, "IDEMPOTENCY_CONFLICT", "같은 요청키로 다른 내용을 보낼 수 없습니다.")
        return previous["response"].model_copy(update={"replayed": True})

    question = get_question(quiz, body.questionId)
    if not any(option["id"] == body.selectedOptionId for option in question["choices"]):
        fail(422, "OPTION_INVALID", "보기 중 하나를 선택하세요.")
    attempts = state.attempts.setdefault(quiz_id, {}).setdefault(question["id"], [])
    if attempts and attempts[-1]["isCorrect"]:
        fail(409, "ALREADY_CORRECT", "이미 맞힌 문제는 오답 재풀이 대상이 아닙니다.")

    correct = body.selectedOptionId == question["correctOptionId"]
    answer = {
        "attemptNo": len(attempts) + 1,
        "selectedOptionId": body.selectedOptionId,
        "isCorrect": correct,
        "points": question["maxPoints"] if correct else 0,
        "answeredAt": timestamp(),
    }
    attempts.append(answer)
    response = AnswerOut(
        questionId=question["id"],
        attemptNo=answer["attemptNo"],
        isCorrect=correct,
        points=answer["points"],
        retryCount=len(attempts) - 1,
        replayed=False,
        results=quiz_results(state, quiz),
    )
    state.idempotency[key] = {"payload": payload, "response": response}
    return response
