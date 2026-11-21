from typing import Literal

from pydantic import BaseModel, Field

from app.core.dto import Area, Input, Kind, Option, Page

QuizStatus = Literal["draft", "reviewed", "published", "withdrawn"]
ProgressStatus = Literal["not_started", "in_progress", "completed"]


class QuestionPatch(Input):
    prompt: str = Field(min_length=1, max_length=10000)
    choices: list[Option] = Field(min_length=2, max_length=4)
    correctOptionId: str = Field(min_length=1, max_length=32)
    explanation: str = Field(min_length=1, max_length=10000)
    sourceSentenceIds: list[str] = Field(min_length=1)
    maxPoints: float = Field(default=1, gt=0, le=100, allow_inf_nan=False)


class QuizDraft(Input):
    sourceId: str
    studentMemberId: str
    inputMaterialId: str | None = None
    title: str = Field(min_length=1, max_length=200)
    questionType: Kind
    questions: list[QuestionPatch] = Field(min_length=1, max_length=20)


class QuizAnswer(Input):
    questionId: str = Field(min_length=1)
    selectedOptionId: str = Field(min_length=1, max_length=32)


class StudentQuestion(BaseModel):
    id: str
    position: int
    questionType: Kind
    prompt: str
    choices: list[Option]
    maxPoints: float


class TeacherQuestion(StudentQuestion):
    correctOptionId: str
    explanation: str
    sourceSentenceIds: list[str]


class WrongQuestion(BaseModel):
    questionId: str
    position: int
    retryCount: int


class ScoreResults(BaseModel):
    quizId: str
    questionCount: int
    submittedQuestionCount: int
    status: ProgressStatus
    firstScore: float | None
    maxScore: float
    firstCorrectCount: int | None
    latestCorrectCount: int
    currentWrongCount: int
    unansweredQuestionCount: int
    retryCounts: dict[str, int]
    wrongQuestions: list[WrongQuestion]


class QuizSummary(BaseModel):
    id: str
    classId: str
    sourceId: str
    studentMemberId: str
    subject: Area
    title: str
    status: QuizStatus
    version: int
    questionType: Kind
    questionCount: int
    publishedAt: str | None = None


class PassageParagraph(BaseModel):
    id: str
    text: str


class Passage(BaseModel):
    paragraphs: list[PassageParagraph]
    translations: list[dict[str, str]] = Field(default_factory=list)


class StudentQuiz(QuizSummary):
    questions: list[StudentQuestion]
    progress: ScoreResults
    passage: Passage


class TeacherQuiz(QuizSummary):
    inputMaterialId: str | None = None
    generationNo: int
    createdBy: str
    createdAt: str
    reviewedAt: str | None = None
    reviewedBy: str | None = None
    publishedBy: str | None = None
    updatedAt: str | None = None
    questions: list[TeacherQuestion]


QuizList = Page[StudentQuiz | TeacherQuiz]


class AnswerOut(BaseModel):
    questionId: str
    attemptNo: int
    isCorrect: bool
    points: float
    retryCount: int
    replayed: bool
    results: ScoreResults


class ExplanationOut(BaseModel):
    questionId: str
    correctOptionId: str
    explanation: str
