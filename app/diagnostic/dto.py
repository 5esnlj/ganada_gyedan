from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.dto import Area, Input, Level, Option, Page


Skill = Literal["listening", "reading"]
Tier = Literal["beginner", "intermediate", "advanced"]
DiagnosticStateCode = Literal["not_started", "in_progress", "completed"]


class DiagnosticStart(Input):
    classId: str = Field(min_length=1, max_length=100)


class DiagnosticAnswer(Input):
    questionId: str = Field(min_length=1, max_length=100)
    selectedOptionId: str | None = Field(min_length=1, max_length=32)


class DiagnosticStatus(BaseModel):
    status: DiagnosticStateCode
    formId: str | None
    totalQuestions: int
    completedQuestions: int
    nextQuestionId: str | None


class DiagnosticOption(Option):
    model_config = ConfigDict(frozen=True)


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


class DiagnosticQuestion(QuestionDisplay):
    isSynthetic: bool


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


class DiagnosticResults(BaseModel):
    formId: str
    isSynthetic: bool
    parts: list[PartResult]
    areas: list[AreaResult]


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
