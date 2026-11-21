from contextlib import nullcontext
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.core.access import active_student_membership, auth, membership
from app.core.errors import ErrorOut, fail
from app.core.state import Store
from app.mariadb.diagnostics import edit_diagnostic
from app.mariadb.store import MariaDBStore

from .dto import (
    DiagnosticAnswer,
    DiagnosticAnswerOut,
    DiagnosticNext,
    DiagnosticResults,
    DiagnosticStart,
    DiagnosticStatus,
    StudentDiagnosticOut,
    StudentList,
)
from .forms import FormProvider, StaticFormProvider
from .service import (
    active_diagnostic,
    can_select_grade_band,
    class_students,
    completed_results,
    diagnostic_state,
    next_question,
    progress,
    start,
    submit_answer,
)


router = APIRouter()


def get_form_provider(request: Request, state: Store) -> FormProvider:
    if isinstance(state, MariaDBStore):
        return StaticFormProvider(state.legacy_form)
    return request.app.state.diagnostic_form_provider


Forms = Annotated[FormProvider, Depends(get_form_provider)]


def _assessment_forms(request: Request, state: Store):
    return state.assessment_forms if isinstance(state, MariaDBStore) else request.app.state.diagnostic_assessment_forms


def _edit(state: Store, user_id: str):
    return edit_diagnostic(state, user_id) if isinstance(state, MariaDBStore) else nullcontext()


@router.get("/diagnostic", tags=["진단"], response_model_exclude_unset=True)
def diagnostic(request: Request, state: Store, forms: Forms) -> DiagnosticStatus:
    user = auth(request, "student")
    record = state.diagnostics.get(user["id"])
    status = progress(record, forms.active_form() if record is None else None)
    status.availableGradeBands = (
        list(_assessment_forms(request, state)) if can_select_grade_band(record) else []
    )
    return status


@router.post(
    "/diagnostic/start", tags=["진단"], response_model_exclude_unset=True,
    responses={503: {"model": ErrorOut, "description": "공개된 진단 문항집 없음"}},
)
def diagnostic_start(
    body: DiagnosticStart, request: Request, state: Store, forms: Forms
) -> DiagnosticStatus:
    user = auth(request, "student")
    with _edit(state, user["id"]):
        return start(state, user, body, forms, _assessment_forms(request, state))


@router.get("/diagnostic/next-question", tags=["진단"], response_model_exclude_unset=True)
def diagnostic_next(request: Request, state: Store) -> DiagnosticNext:
    return next_question(active_diagnostic(state, auth(request, "student")))


@router.post("/diagnostic/answers", tags=["진단"], response_model_exclude_unset=True)
def diagnostic_answer(body: DiagnosticAnswer, request: Request, state: Store) -> DiagnosticAnswerOut:
    user = auth(request, "student")
    with _edit(state, user["id"]):
        return submit_answer(active_diagnostic(state, user), body)


@router.get("/diagnostic/results", tags=["진단"], response_model_exclude_unset=True)
def diagnostic_results(request: Request, state: Store) -> DiagnosticResults:
    user = auth(request, "student")
    results = completed_results(state, user["id"])
    if results is None:
        fail(409, "DIAGNOSTIC_INCOMPLETE", "진단이 아직 완료되지 않았습니다.")
    return results


@router.get("/classes/{classId}/students", tags=["학생 수준"])
def students(classId: str, request: Request, state: Store) -> StudentList:
    user = auth(request, "teacher")
    membership(state, user, classId, "teacher")
    return StudentList(items=class_students(state, classId))


@router.get(
    "/classes/{classId}/students/{memberId}/diagnostic",
    tags=["학생 수준"], response_model_exclude_unset=True,
)
def student_diagnostic(
    classId: str, memberId: str, request: Request, state: Store
) -> StudentDiagnosticOut:
    user = auth(request, "teacher")
    membership(state, user, classId, "teacher")
    member = active_student_membership(state, memberId, classId)
    if member is None:
        fail(404, "NOT_FOUND", "대상 학생을 찾을 수 없습니다.")
    student_id = member["userId"]
    return StudentDiagnosticOut(
        studentMemberId=memberId, status=diagnostic_state(state, student_id),
        results=completed_results(state, student_id),
    )
