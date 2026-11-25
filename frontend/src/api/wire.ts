import type { Role } from '../types';
export type AreaCode = 'korean' | 'math' | 'social' | 'science' | 'daily';
export type LevelCode = 'below_beginner' | 'beginner' | 'intermediate' | 'advanced';
export type DiagnosticState = 'not_started' | 'in_progress' | 'completed';
export type GradeBand = 'g34' | 'g56';
export type DiagnosticSkill = 'listening' | 'reading';
export type DiagnosticStatus = {
  status: DiagnosticState;
  formId: string | null;
  totalQuestions: number;
  completedQuestions: number;
  nextQuestionId: string | null;
  gradeBand?: GradeBand;
  availableGradeBands?: GradeBand[];
};
export type DiagnosticSpan = { text: string; style: 'plain' | 'strong' | 'underline' };
export type DiagnosticQuestion = {
  id: string;
  sequence: number;
  area: AreaCode;
  skill: DiagnosticSkill;
  prompt: string;
  options: { id: string; text: string; imageUrl?: string }[];
  audioUrl: string | null;
  stimulusText: string;
  isSynthetic: boolean;
  imageUrl?: string;
  word?: string;
  stimulusTable?: { headers: string[]; rows: string[][] };
  promptSpans?: DiagnosticSpan[];
  stimulusSpans?: DiagnosticSpan[];
  sectionNumber?: number;
  sectionTotal?: number;
  maxPoints?: number;
  listeningPlaysRemaining?: number;
};
export type DiagnosticNext = { progress: DiagnosticStatus; question: DiagnosticQuestion | null };
export type DiagnosticAnswerOut = { progress: DiagnosticStatus; replayed: boolean };
export type DiagnosticListenOut = { remainingPlays: number };
export type AssessmentSection = { score: number; maxScore: number; percent: number; level: LevelCode };
export type AssessmentSummary = {
  gradeBand: GradeBand;
  listening: AssessmentSection;
  reading: AssessmentSection;
  overall: { score: number; maxScore: number; level: LevelCode };
  subjects: (AssessmentSection & {
    area: AreaCode;
    scoreLevel: LevelCode;
    criterionLevel: LevelCode;
    tierCounts: Record<'beginner' | 'intermediate' | 'advanced', { correct: number; total: number }>;
  })[];
  communication: {
    score: number; maxScore: number; percent: number; questionCount: number;
    status: 'adequate' | 'needs_improvement';
  };
  areaParts?: (AssessmentSection & { area: AreaCode; skill: DiagnosticSkill })[];
};
export type DiagnosticResults = {
  formId: string; isSynthetic: boolean; parts: PartResult[]; areas: AreaResult[];
  assessmentSummary?: AssessmentSummary;
};
export type ListOut<T> = { items: T[]; nextCursor: string | null };
export type SessionOut = { user: { id: string; role: Role; displayName: string; nativeLanguage: string }; csrfToken: string; nextAction: 'classes' | 'diagnostic' | 'join_class' };
export type ClassOut = { id: string; name: string; gradeLabel: string; academicYear: number; teacherSubject: AreaCode | null };
export type PartResult = { area: AreaCode; skill: 'listening' | 'reading'; score: number; maxScore: number; level: LevelCode };
export type AreaResult = { area: AreaCode; score: number; maxScore: number; level: LevelCode };
export type ScoreResults = { status: DiagnosticState; questionCount: number; submittedQuestionCount: number; firstCorrectCount: number | null; firstScore: number | null; currentWrongCount: number; unansweredQuestionCount: number };
export type TeacherDashboardOut = { classId: string; className: string; selectedArea: AreaCode; teacherSubject: AreaCode; studentCount: number; levelCounts: Record<LevelCode | 'not_diagnosed', number>; pendingReviewQuizSetCount: number; students: { studentMemberId: string; displayName: string; nativeLanguage: string; diagnosticStatus: DiagnosticState; parts: PartResult[]; aggregate: AreaResult | null; latestQuiz: ScoreResults | null }[] };
export type SourceOut = { id: string; subject: AreaCode; unit: string; title: string; createdAt: string };
/** start is a Unicode code point offset in the converted paragraph. */
export type PhraseChange = { start: number; original: string; replacement: string };
export type MaterialParagraph = { id: string; text: string; page?: number | null; phraseChanges?: PhraseChange[] };
export type MaterialOut = { id: string; subject: AreaCode; unit: string; title: string; publishedAt: string | null; createdAt: string; isRecentPublished: boolean };
export type StudentDashboardOut = { classId: string; className: string; displayName: string; selectedArea: AreaCode; parts: PartResult[]; aggregate: AreaResult; unansweredQuestionCount: number; retryQuestionCount: number; newMaterials: MaterialOut[]; quizzes: { id: string; title: string; subject: AreaCode; progress: ScoreResults }[] };
