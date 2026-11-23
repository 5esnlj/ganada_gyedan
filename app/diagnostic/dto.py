from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.dto import Area, Input, Level, Option, Page


GradeBand = Literal["g34", "g56"]
Skill = Literal["listening", "reading"]
Tier = Literal["beginner", "intermediate", "advanced"]
DiagnosticStateCode = Literal["not_started", "in_progress", "completed"]


class DiagnosticStart(Input):
    classId: str = Field(min_length=1, max_length=100)
    gradeBand: GradeBand | None = None


class DiagnosticAnswer(Input):
    questionId: str = Field(min_length=1, max_length=100)
    selectedOptionId: str | None = Field(min_length=1, max_length=32)


class DiagnosticStatus(BaseModel):
    status: DiagnosticStateCode
    formId: str | None
    totalQuestions: int
    completedQuestions: int
    nextQuestionId: str | None
    gradeBand: GradeBand | None = None
    availableGradeBands: list[GradeBand] = Field(default_factory=list)


class DiagnosticOption(Option):
    model_config = ConfigDict(frozen=True)
    imageUrl: str | None = None


class DiagnosticTextSpan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    text: str
    style: Literal["plain", "strong", "underline"]


class DiagnosticTable(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


class QuestionDisplay(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1, max_length=100)
    sequence: int = Field(ge=1)
    area: Area
    skill: Skill
    prompt: str = Field(min_length=1)
    options: tuple[DiagnosticOption, ...] = Field(min_length=2)
    audioUrl: str | None
    stimulusText: str
    word: str | None = None
    imageUrl: str | None = None
    stimulusTable: DiagnosticTable | None = None
    promptSpans: tuple[DiagnosticTextSpan, ...] | None = None
    stimulusSpans: tuple[DiagnosticTextSpan, ...] | None = None


class DiagnosticQuestion(QuestionDisplay):
    isSynthetic: bool
    maxPoints: float | None = None
    sectionNumber: int | None = None
    sectionTotal: int | None = None


class DiagnosticNext(BaseModel):
    progress: DiagnosticStatus
    question: DiagnosticQuestion | None


class DiagnosticAnswerOut(BaseModel):
    progress: DiagnosticStatus
    replayed: bool


class PartResult(BaseModel):
    area: Area
    skill: Skill
    score: float
    maxScore: Literal[100] = 100
    level: Level


class AreaResult(BaseModel):
    area: Area
    score: float
    maxScore: Literal[200] = 200
    level: Level


class AssessmentScore(BaseModel):
    score: float
    maxScore: float
    percent: float


class AssessmentSkillResult(AssessmentScore):
    level: Level


class AssessmentTierCount(BaseModel):
    correct: int
    total: int


class AssessmentSubjectResult(AssessmentScore):
    area: Literal["korean", "math", "social", "science"]
    scoreLevel: Level
    criterionLevel: Level
    level: Level
    tierCounts: dict[Tier, AssessmentTierCount]


class AssessmentOverallResult(BaseModel):
    score: float
    maxScore: Literal[200]
    level: Level


class AssessmentCommunicationResult(AssessmentScore):
    questionCount: int
    status: Literal["adequate", "needs_improvement"]


class AssessmentSummary(BaseModel):
    gradeBand: GradeBand
    listening: AssessmentSkillResult
    reading: AssessmentSkillResult
    overall: AssessmentOverallResult
    subjects: list[AssessmentSubjectResult]
    communication: AssessmentCommunicationResult


class DiagnosticResults(BaseModel):
    formId: str
    isSynthetic: bool
    parts: list[PartResult]
    areas: list[AreaResult]
    assessmentSummary: AssessmentSummary | None = None


class StudentRow(BaseModel):
    id: str
    userId: str
    displayName: str
    nativeLanguage: str
    diagnosticStatus: DiagnosticStateCode
    partResults: list[PartResult]
    areaResults: list[AreaResult]


class StudentList(Page[StudentRow]):
    pass


class StudentDiagnosticOut(BaseModel):
    studentMemberId: str
    status: DiagnosticStateCode
    results: DiagnosticResults | None
