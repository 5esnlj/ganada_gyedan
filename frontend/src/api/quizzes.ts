import { request, requestWithMeta } from './client';
import type { AreaCode, LevelCode, ListOut } from './wire';

const classPath = (classId: string) => `/classes/${encodeURIComponent(classId)}`;
const quizPath = (quizId: string) => `/quizzes/${encodeURIComponent(quizId)}`;

export type QuizCategory = 'term' | 'fact' | 'relation' | 'application' | 'comparison';
export type QuizStatus = 'draft' | 'reviewed' | 'published' | 'withdrawn';
export type Choice = { id: string; text: string };
export type PassageOut = { paragraphs: { id: string; text: string }[]; translations: Record<string, string>[] };

export type ScoreResults = {
  quizId: string; questionCount: number; submittedQuestionCount: number;
  status: 'not_started' | 'in_progress' | 'completed';
  firstScore: number | null; maxScore: number; firstCorrectCount: number | null; latestCorrectCount: number;
  currentWrongCount: number; unansweredQuestionCount: number; retryCounts: Record<string, number>;
  wrongQuestions: { questionId: string; position: number; retryCount: number }[];
  answeredQuestionIds: string[];
};

type QuizBase = {
  id: string; classId: string; sourceId: string; studentMemberId: string; subject: AreaCode; title: string;
  status: QuizStatus; version: number; questionType: 'true_false' | 'multiple_choice_4'; questionCount: number;
  publishedAt: string | null;
};

export type StudentQuestion = {
  id: string; position: number; prompt: string; choices: Choice[]; maxPoints: number; isChallenge: boolean;
};
export type TeacherQuestion = StudentQuestion & {
  correctOptionId: string; explanation: string; sourceSentenceIds: string[];
  category: QuizCategory | null; firstAttemptCorrect: boolean | null;
};
export type QuizGenerationInfo = {
  promptVersion: string; baseLevel: LevelCode; plan: { category: QuizCategory; isChallenge: boolean }[];
  adjustment: string | null; nextAdjustment: string | null;
};
export type TeacherQuiz = QuizBase & {
  inputMaterialId: string | null; generationNo: number; createdAt: string; questions: TeacherQuestion[];
  generation: QuizGenerationInfo | null; progress: ScoreResults | null; passage: PassageOut | null;
};
export type StudentQuiz = QuizBase & { questions: StudentQuestion[]; progress: ScoreResults; passage: PassageOut };

export type QuizJob = {
  id: string; classId: string; sourceId: string; studentMemberId: string; inputMaterialId: string | null;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'unknown';
  quizId: string | null; errorCode: string | null; errorMessage: string | null; createdAt: string;
};
export type QuestionEdit = {
  prompt: string; choices: Choice[]; correctOptionId: string; explanation: string; sourceSentenceIds: string[]; maxPoints: number;
};
export type AnswerOut = {
  questionId: string; attemptNo: number; isCorrect: boolean; points: number; retryCount: number; replayed: boolean;
  results: ScoreResults;
};
export type Explanation = { questionId: string; correctOptionId: string; explanation: string; sourceSentenceIds: string[] };

export const quizApi = {
  startJobs: (classId: string, input: { sourceId: string; studentMemberIds: string[] }, requestId: string) =>
    request<ListOut<QuizJob>>(`${classPath(classId)}/generation-jobs`, {
      method: 'POST', headers: { 'Idempotency-Key': requestId },
      body: JSON.stringify({ ...input, operation: 'generate_quiz' }),
    }),
  jobs: (classId: string) => request<ListOut<QuizJob>>(`${classPath(classId)}/generation-jobs?operation=generate_quiz`),
  teacherQuizzes: (classId: string, subject: AreaCode) =>
    request<ListOut<TeacherQuiz>>(`${classPath(classId)}/quizzes?subject=${encodeURIComponent(subject)}`),
  teacherQuiz: (quizId: string) => requestWithMeta<TeacherQuiz>(quizPath(quizId)),
  editQuestion: (quizId: string, questionId: string, body: QuestionEdit, version: string) =>
    requestWithMeta<TeacherQuiz>(`${quizPath(quizId)}/questions/${encodeURIComponent(questionId)}`, {
      method: 'PATCH', headers: { 'If-Match': version }, body: JSON.stringify(body),
    }),
  transition: (quizId: string, action: 'review' | 'publish', version: string) =>
    requestWithMeta<TeacherQuiz>(`${quizPath(quizId)}/${action}`, { method: 'POST', headers: { 'If-Match': version } }),
  studentQuizzes: (classId: string) => request<ListOut<StudentQuiz>>(`${classPath(classId)}/quizzes`),
  studentQuiz: (quizId: string) => request<StudentQuiz>(quizPath(quizId)),
  answer: (quizId: string, questionId: string, selectedOptionId: string, requestId: string) =>
    request<AnswerOut>(`${quizPath(quizId)}/answers`, {
      method: 'POST', headers: { 'Idempotency-Key': requestId },
      body: JSON.stringify({ questionId, selectedOptionId }),
    }),
  explanation: (quizId: string, questionId: string) =>
    request<Explanation>(`${quizPath(quizId)}/questions/${encodeURIComponent(questionId)}/explanation`),
};
