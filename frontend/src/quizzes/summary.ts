/** 확인 문제 화면이 쓰는 표시·집계 규칙. 화면 코드와 분리해 테스트한다. */
import type { QuizCategory, QuizStatus, ScoreResults, TeacherQuiz } from '../api/quizzes';
import type { LevelCode } from '../api/wire';

export const CATEGORY_LABELS: Record<QuizCategory, string> = {
  term: '용어', fact: '사실', relation: '관계', application: '적용', comparison: '비교',
};
export const CATEGORY_ORDER: QuizCategory[] = ['term', 'fact', 'relation', 'application', 'comparison'];
export const QUIZ_STATUS_LABELS: Record<QuizStatus, string> = {
  draft: '초안', reviewed: '검토 완료', published: '배포됨', withdrawn: '회수됨',
};
export const LEVEL_ORDER: LevelCode[] = ['below_beginner', 'beginner', 'intermediate', 'advanced'];

/** 유형별 구성 요약: "용어 2 · 사실 2 · 관계(도전) 1" */
export function planText(plan: { category: QuizCategory; isChallenge: boolean }[]): string {
  const counts = new Map<string, number>();
  for (const slot of plan) {
    const key = CATEGORY_LABELS[slot.category] + (slot.isChallenge ? '(도전)' : '');
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return [...counts].map(([key, n]) => `${key} ${n}`).join(' · ');
}

/** 학생이 처음 푼 결과를 유형별로 센다. 아직 안 푼 문항은 빼고, 도전 문항은 따로 센다. */
export function firstAttemptSummary(quiz: Pick<TeacherQuiz, 'questions'>) {
  const byCategory: Partial<Record<QuizCategory, { correct: number; total: number }>> = {};
  const challenge = { correct: 0, total: 0 };
  let answered = 0;
  for (const q of quiz.questions) {
    if (q.firstAttemptCorrect === null) continue;
    answered += 1;
    if (q.category) {
      const row = byCategory[q.category] ?? { correct: 0, total: 0 };
      row.total += 1; row.correct += q.firstAttemptCorrect ? 1 : 0;
      byCategory[q.category] = row;
    }
    if (q.isChallenge) { challenge.total += 1; challenge.correct += q.firstAttemptCorrect ? 1 : 0; }
  }
  return { answered, byCategory, challenge };
}

export function categoryResultText(summary: ReturnType<typeof firstAttemptSummary>): string {
  return CATEGORY_ORDER.flatMap((c) => {
    const row = summary.byCategory[c];
    return row ? [`${CATEGORY_LABELS[c]} ${row.correct}/${row.total}`] : [];
  }).join(' · ');
}

/** 학생 목록 상태: 풀기 전 / 다시 풀 문제 N / 완료 a/b (HANDOFF 학생 06) */
export function progressLabel(progress: ScoreResults): string {
  if (progress.status === 'not_started') return '풀기 전';
  if (progress.status === 'in_progress') return `푸는 중 ${progress.submittedQuestionCount}/${progress.questionCount}`;
  if (progress.currentWrongCount > 0) return `다시 풀 문제 ${progress.currentWrongCount}`;
  return `완료 ${progress.firstCorrectCount ?? 0}/${progress.questionCount}`;
}

/** 아직 처음 답하지 않은 첫 문항. 모두 답했으면 null */
export function nextUnanswered(questionIds: string[], answered: Set<string>): number | null {
  const index = questionIds.findIndex((id) => !answered.has(id));
  return index < 0 ? null : index;
}

/** 수준 묶음 탭: 진단 단계별로 문제 묶음을 모은다. 생성 기록이 없으면 '기타'. */
export function groupByLevel<T extends Pick<TeacherQuiz, 'generation'>>(quizzes: T[]) {
  const groups = new Map<LevelCode | 'other', T[]>();
  for (const quiz of quizzes) {
    const key = quiz.generation?.baseLevel ?? 'other';
    groups.set(key, [...(groups.get(key) ?? []), quiz]);
  }
  return [...LEVEL_ORDER, 'other' as const].filter((key) => groups.has(key)).map((key) => ({ key, quizzes: groups.get(key)! }));
}
