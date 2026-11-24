from typing import Annotated, Literal

from fastapi import APIRouter, Header, Request, Response

from app.core.access import active_student_membership, auth
from app.core.dto import Area
from app.core.errors import fail
from app.core.review import check_version, set_etag
from app.core.state import Store
from app.diagnostic.service import completed_results
from app.generation import credentials, solar
from app.generation.jobs import request_keys
from app.mariadb import quizzes as database_quizzes
from app.mariadb.store import MariaDBStore
from app.materials.service import require_source

from . import jobs
from .dto import (
    AnswerOut,
    QuizGenerate,
    ExplanationOut,
    QuestionPatch,
    QuizAnswer,
    QuizList,
    ScoreResults,
    StudentQuiz,
    TeacherQuiz,
)
from .service import (
    edit_question,
    get_question,
    quiz_access,
    quiz_output,
    quiz_results,
    submit_answer,
    transition_quiz,
    visible_quizzes,
)

from .worker import run_quiz_jobs

router = APIRouter(tags=["학습 문제"])


@router.get("/classes/{classId}/quizzes", response_model=QuizList)
def list_quizzes(
    classId: str, request: Request, state: Store, subject: Area | None = None
) -> QuizList:
    user = auth(request, learning=True)
    items = [quiz_output(state, quiz, user) for quiz in visible_quizzes(state, user, classId, subject)]
    return QuizList(items=items, nextCursor=None)


@router.get("/quizzes/{quizId}", response_model=StudentQuiz | TeacherQuiz)
def get_quiz(
    quizId: str, request: Request, response: Response, state: Store
) -> StudentQuiz | TeacherQuiz:
    user = auth(request, learning=True)
    quiz = quiz_access(state, user, quizId)
    set_etag(response, quiz)
    return quiz_output(state, quiz, user)


@router.patch("/quizzes/{quizId}/questions/{questionId}", response_model=TeacherQuiz)
def patch_question(
    quizId: str,
    questionId: str,
    body: QuestionPatch,
    request: Request,
    response: Response,
    state: Store,
) -> TeacherQuiz:
    user = auth(request, "teacher")
    quiz = quiz_access(state, user, quizId, write=True)
    check_version(request, quiz)
    expected = quiz["version"]
    edit_question(state, quiz, questionId, body)
    if isinstance(state, MariaDBStore):
        database_quizzes.save_revision(quiz, user["id"], expected, get_question(quiz, questionId))
    set_etag(response, quiz)
    return quiz_output(state, quiz, user)


def _change_status(
    quiz_id: str,
    request: Request,
    response: Response,
    state: Store,
    target: Literal["reviewed", "published"],
) -> TeacherQuiz:
    user = auth(request, "teacher")
    quiz = quiz_access(state, user, quiz_id, write=True)
    check_version(request, quiz)
    expected = quiz["version"]
    transition_quiz(state, quiz, user, target)
    if isinstance(state, MariaDBStore):
        database_quizzes.save_revision(quiz, user["id"], expected)
    set_etag(response, quiz)
    return quiz_output(state, quiz, user)


@router.post("/quizzes/{quizId}/review", response_model=TeacherQuiz)
def review_quiz(quizId: str, request: Request, response: Response, state: Store) -> TeacherQuiz:
    return _change_status(quizId, request, response, state, "reviewed")


@router.post("/quizzes/{quizId}/publish", response_model=TeacherQuiz)
def publish_quiz(quizId: str, request: Request, response: Response, state: Store) -> TeacherQuiz:
    return _change_status(quizId, request, response, state, "published")


@router.post("/quizzes/{quizId}/answers", response_model=AnswerOut, tags=["학습 풀이"])
def answer_quiz(
    quizId: str,
    body: QuizAnswer,
    request: Request,
    state: Store,
    request_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> AnswerOut:
    user = auth(request, "student", learning=True)
    quiz = quiz_access(state, user, quizId)
    if isinstance(state, MariaDBStore):
        return database_quizzes.save_answer(state, quiz, user, body, request_key)
    return submit_answer(state, quiz, user, body, request_key)


@router.get("/quizzes/{quizId}/results", response_model=ScoreResults, tags=["학습 풀이"])
def get_results(quizId: str, request: Request, state: Store) -> ScoreResults:
    user = auth(request, learning=True)
    return quiz_results(state, quiz_access(state, user, quizId))


@router.get(
    "/quizzes/{quizId}/questions/{questionId}/explanation",
    response_model=ExplanationOut,
    tags=["학습 풀이"],
)
def get_explanation(quizId: str, questionId: str, request: Request, state: Store) -> ExplanationOut:
    user = auth(request, learning=True)
    quiz = quiz_access(state, user, quizId)
    question = get_question(quiz, questionId)
    if user["role"] == "student" and not state.attempts.get(quizId, {}).get(questionId):
        fail(403, "ANSWER_REQUIRED", "먼저 이 문제에 답해 주세요.")
    return ExplanationOut(
        questionId=questionId,
        correctOptionId=question["correctOptionId"],
        explanation=question["explanation"],
        sourceSentenceIds=question["sourceSentenceIds"],
    )


def _latest_published_material(state, source_id: str, member_id: str) -> str | None:
    published = [
        m for m in state.materials.values()
        if m["sourceId"] == source_id and m["studentMemberId"] == member_id and m["status"] == "published"
    ]
    return max(published, key=lambda m: m.get("revision", 0))["id"] if published else None


def start_quiz_jobs(classId, body: QuizGenerate, request, state, background_tasks, request_key, teacher):
    """Queue one quiz per student using a published edition or explicitly approved passage."""
    batch_key, digest = request_keys(body, request_key)
    if len(set(body.studentMemberIds)) != len(body.studentMemberIds):
        fail(422, "VALIDATION_ERROR", "같은 학생을 중복 선택할 수 없습니다.")
    if body.inputMaterialId is not None and (len(body.studentMemberIds) != 1 or not body.useStudentMaterials):
        fail(422, "VALIDATION_ERROR", "지정한 전환본은 한 학생을 대상으로 출제할 때 사용할 수 있습니다.")
    source = require_source(state, teacher, body.sourceId)
    if source["classId"] != classId:
        fail(404, "NOT_FOUND", "담당 클래스의 원본 자료를 선택해 주세요.")
    approved = set(body.approvedSentenceIds)
    if len(approved) != len(body.approvedSentenceIds) or not approved.issubset(row["id"] for row in source["sentences"]):
        fail(422, "QUESTION_INVALID", "원본 지문의 문장 선택을 다시 확인하세요.")
    keys = credentials.rows(state, teacher["id"])
    if not keys:
        fail(409, "SOLAR_KEY_REQUIRED", solar.ERROR_MESSAGES["SOLAR_KEY_REQUIRED"])
    model = solar.model_name()
    candidates = []
    for member_id in body.studentMemberIds:
        member = active_student_membership(state, member_id, classId)
        if not member:
            fail(404, "NOT_FOUND", "이 클래스의 학생을 선택해 주세요.")
        diagnosis = completed_results(state, member["userId"])
        if diagnosis is None:
            fail(409, "STUDENT_DIAGNOSTIC_REQUIRED", "진단평가를 완료한 학생을 선택해 주세요.")
        input_id = body.inputMaterialId
        if input_id is not None:
            material = state.materials.get(input_id)
            if not material or (material["classId"], material["sourceId"], material["studentMemberId"], material["status"]) != (
                classId, body.sourceId, member_id, "published",
            ):
                fail(409, "INPUT_MATERIAL_UNPUBLISHED", "대상 학생에게 배포한 전환본을 선택해 주세요.")
        elif body.useStudentMaterials:
            input_id = _latest_published_material(state, body.sourceId, member_id)
        if input_id is None and not approved:
            fail(422, "QUIZ_PASSAGE_REQUIRED", "배포된 전환본이 없는 학생이 있습니다. 원본에서 출제할 문장을 선택해 주세요.")
        candidates.append({
            "classId": classId, "sourceId": body.sourceId, "studentMemberId": member_id,
            "inputMaterialId": input_id,
            "credentialId": keys[0]["id"], "createdBy": teacher["id"], "model": model,
            "snapshot": {"batchKey": batch_key, "digest": digest, "diagnosis": diagnosis.model_dump(mode="json"),
                         "approvedSentenceIds": body.approvedSentenceIds if input_id is None else None},
        })
    batch, created = jobs.batch(state, teacher["id"], classId, candidates)
    if created:
        background_tasks.add_task(run_quiz_jobs, request.app, [job["id"] for job in batch])
    return {"items": [jobs.public(job) for job in batch]}


def list_quiz_jobs(classId: str, state: Store, teacher):
    return {"items": [jobs.public(job) for job in jobs.list_owned(state, teacher["id"], classId)]}
