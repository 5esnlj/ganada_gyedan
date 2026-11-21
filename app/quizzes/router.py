from typing import Annotated, Literal

from fastapi import APIRouter, Header, Request, Response

from app.core.access import auth
from app.core.dto import Area
from app.core.errors import fail
from app.core.review import check_version, set_etag
from app.core.state import Store

from .dto import (
    AnswerOut,
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
    edit_question(state, quiz, questionId, body)
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
    transition_quiz(state, quiz, user, target)
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
    )
