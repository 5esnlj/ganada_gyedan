from fastapi import APIRouter, Request

from app.core.access import auth, membership
from app.core.dto import Area, Page
from app.core.state import Store

from .dto import ActivityRow, StudentDashboard, TeacherDashboard
from .service import recent_activity, student_dashboard, teacher_dashboard

router = APIRouter(tags=["대시보드"])


@router.get("/classes/{classId}/dashboard", response_model=TeacherDashboard | StudentDashboard)
def dashboard(classId: str, request: Request, state: Store, area: Area | None = None):
    user = auth(request, learning=True)
    member = membership(state, user, classId)
    if user["role"] == "teacher":
        subject = member["teacherSubject"]
        return teacher_dashboard(state, user, classId, subject, area or subject)
    return student_dashboard(state, user, classId, area or "daily")


@router.get("/classes/{classId}/activity", response_model=Page[ActivityRow])
def activity(classId: str, request: Request, state: Store):
    user = auth(request, "teacher")
    return Page[ActivityRow](items=recent_activity(state, user, classId))
