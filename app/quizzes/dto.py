from typing import Literal

from pydantic import BaseModel, Field

from app.core.dto import Area, Input, Kind, Level, Option, Page

QuizStatus = Literal["draft", "reviewed", "published", "withdrawn"]
ProgressStatus = Literal["not_started", "in_progress", "completed"]
Category = Literal["term", "fact", "relation", "application", "comparison"]
JobStatus = Literal["queued", "running", "succeeded", "failed", "unknown"]


class QuestionPatch(Input):
    prompt: str = Field(min_length=1, max_length=10000)
    choices: list[Option] = Field(min_length=2, max_length=4)
    correctOptionId: str = Field(min_length=1, max_length=32)
    explanation: str = Field(min_length=1, max_length=10000)
    sourceSentenceIds: list[str] = Field(min_length=1)
    maxPoints: float = Field(default=1, ge=0.01, le=100, multiple_of=0.01, allow_inf_nan=False)


class QuizDraft(Input):
    sourceId: str
    studentMemberId: str
    inputMaterialId: str | None = Field(default=None, min_length=1, max_length=80)
    approvedSentenceIds: list[str] | None = None
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
    isChallenge: bool = False


class TeacherQuestion(StudentQuestion):
    correctOptionId: str
    explanation: str
    sourceSentenceIds: list[str]
    category: Category | None = None
    firstAttemptCorrect: bool | None = None


class PlanSlot(BaseModel):
    category: Category
    isChallenge: bool


class QuizGenerationInfo(BaseModel):
    promptVersion: str
    baseLevel: Level
    plan: list[PlanSlot]
    adjustment: str | None = None
    nextAdjustment: str | None = None


class QuizGenerate(Input):
    operation: Literal["generate_quiz"]
    sourceId: str = Field(min_length=1, max_length=80)
    studentMemberIds: list[str] = Field(min_length=1, max_length=30)
    useStudentMaterials: bool = True
    inputMaterialId: str | None = Field(default=None, min_length=1, max_length=80)
    questionType: Literal["multiple_choice_4"] = "multiple_choice_4"
    questionCount: Literal[5] = 5
    approvedSentenceIds: list[str] = Field(default_factory=list, max_length=80)


class QuizJobOut(BaseModel):
    id: str
    classId: str
    sourceId: str
    studentMemberId: str
    credentialId: str
    createdBy: str
    operation: Literal["generate_quiz"] = "generate_quiz"
    inputMaterialId: str | None = None
    status: JobStatus
    quizId: str | None = None
    resultId: str | None = None
    resultType: Literal["quiz"] | None = None
    errorCode: str | None = None
    errorMessage: str | None = None
    createdAt: str


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
    answeredQuestionIds: list[str] = Field(default_factory=list)


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
    generation: QuizGenerationInfo | None = None
    progress: ScoreResults | None = None
    passage: Passage | None = None


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
    sourceSentenceIds: list[str] = Field(default_factory=list)
