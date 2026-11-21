from collections import Counter

from app.core.access import active_student_membership
from app.core.dto import Area
from app.core.errors import fail
from app.core.state import MemoryStore, Record
from app.diagnostic.service import class_students, completed_results
from app.materials.service import list_materials
from app.quizzes.service import last_answered_at, quiz_results, visible_quizzes

from .dto import (
    ActivityRow,
    DashboardStudentRow,
    LevelCounts,
    StudentDashboard,
    StudentDashboardQuiz,
    TeacherDashboard,
)


def _active_assignments(state: MemoryStore, user: Record, class_id: str) -> list[Record]:
    return [
        quiz for quiz in visible_quizzes(state, user, class_id)
        if active_student_membership(state, quiz["studentMemberId"], class_id) is not None
    ]


def _latest_first(quiz: Record) -> tuple[str, str]:
    return quiz["publishedAt"], quiz["id"]


def teacher_dashboard(
    state: MemoryStore, user: Record, class_id: str, subject: Area, selected: Area
) -> TeacherDashboard:
    quizzes = _active_assignments(state, user, class_id)
    published = sorted((quiz for quiz in quizzes if quiz["status"] == "published"), key=_latest_first)
    latest_by_student = {quiz["studentMemberId"]: quiz for quiz in published}
    rows = []
    counts = Counter()
    for student in class_students(state, class_id):
        result = next((item for item in student.areaResults if item.area == selected), None)
        counts[result.level if result else "not_diagnosed"] += 1
        latest = latest_by_student.get(student.id)
        rows.append(DashboardStudentRow(
            studentMemberId=student.id,
            displayName=student.displayName,
            nativeLanguage=student.nativeLanguage,
            diagnosticStatus=student.diagnosticStatus,
            selectedArea=selected,
            parts=[part for part in student.partResults if part.area == selected],
            aggregate=result,
            latestQuiz=quiz_results(state, latest) if latest else None,
        ))
    return TeacherDashboard(
        classId=class_id,
        className=state.classes[class_id]["name"],
        selectedArea=selected,
        teacherSubject=subject,
        studentCount=len(rows),
        levelCounts=LevelCounts(**counts),
        pendingReviewQuizSetCount=sum(quiz["status"] == "draft" for quiz in quizzes),
        students=rows,
    )


def student_dashboard(state: MemoryStore, user: Record, class_id: str, selected: Area) -> StudentDashboard:
    result = completed_results(state, user["id"])
    aggregate = next((item for item in result.areas if item.area == selected), None) if result else None
    if not result or aggregate is None:
        fail(409, "DIAGNOSTIC_INCOMPLETE", "선택한 영역의 진단 결과가 없습니다.")
    quizzes = [
        StudentDashboardQuiz(
            id=quiz["id"], title=quiz["title"], subject=quiz["subject"], progress=quiz_results(state, quiz)
        )
        for quiz in visible_quizzes(state, user, class_id)
    ]
    materials = sorted(
        list_materials(state, user, class_id),
        key=lambda item: (item.publishedAt or "", item.id),
        reverse=True,
    )
    return StudentDashboard(
        classId=class_id,
        className=state.classes[class_id]["name"],
        displayName=user["displayName"],
        selectedArea=selected,
        parts=[part for part in result.parts if part.area == selected],
        aggregate=aggregate,
        unansweredQuestionCount=sum(quiz.progress.unansweredQuestionCount for quiz in quizzes),
        retryQuestionCount=sum(quiz.progress.currentWrongCount for quiz in quizzes),
        assignedQuizSetCount=len(quizzes),
        newMaterials=materials[:3],
        quizzes=quizzes,
    )


def recent_activity(state: MemoryStore, user: Record, class_id: str) -> list[ActivityRow]:
    published = (quiz for quiz in _active_assignments(state, user, class_id) if quiz["status"] == "published")
    return [
        ActivityRow(
            quizId=quiz["id"],
            title=quiz["title"],
            studentMemberId=quiz["studentMemberId"],
            publishedAt=quiz["publishedAt"],
            lastAnsweredAt=last_answered_at(state, quiz["id"]),
            results=quiz_results(state, quiz),
        )
        for quiz in sorted(published, key=_latest_first, reverse=True)
    ]
