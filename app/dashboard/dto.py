from pydantic import BaseModel

from app.core.dto import Area
from app.diagnostic.dto import AreaResult, DiagnosticStateCode, PartResult
from app.materials.dto import MaterialOut
from app.quizzes.dto import ScoreResults


class LevelCounts(BaseModel):
    below_beginner: int = 0
    beginner: int = 0
    intermediate: int = 0
    advanced: int = 0
    not_diagnosed: int = 0


class DashboardStudentRow(BaseModel):
    studentMemberId: str
    displayName: str
    nativeLanguage: str
    diagnosticStatus: DiagnosticStateCode
    selectedArea: Area
    parts: list[PartResult]
    aggregate: AreaResult | None
    latestQuiz: ScoreResults | None


class TeacherDashboard(BaseModel):
    classId: str
    className: str
    selectedArea: Area
    teacherSubject: Area
    studentCount: int
    levelCounts: LevelCounts
    pendingReviewQuizSetCount: int
    students: list[DashboardStudentRow]


class StudentDashboardQuiz(BaseModel):
    id: str
    title: str
    subject: Area
    progress: ScoreResults


class StudentDashboard(BaseModel):
    classId: str
    className: str
    displayName: str
    selectedArea: Area
    parts: list[PartResult]
    aggregate: AreaResult
    unansweredQuestionCount: int
    retryQuestionCount: int
    assignedQuizSetCount: int
    newMaterials: list[MaterialOut]
    quizzes: list[StudentDashboardQuiz]


class ActivityRow(BaseModel):
    quizId: str
    title: str
    studentMemberId: str
    publishedAt: str
    lastAnsweredAt: str | None
    results: ScoreResults
