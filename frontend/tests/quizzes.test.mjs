import { test } from 'node:test';
import assert from 'node:assert/strict';
import { categoryResultText, firstAttemptSummary, groupByLevel, nextUnanswered, planText, progressLabel } from '../.test-build/quizzes/summary.js';

const q = (category, isChallenge, firstAttemptCorrect) => ({ category, isChallenge, firstAttemptCorrect });

test('plan text counts categories and marks challenge items', () => {
  assert.equal(planText([
    { category: 'term', isChallenge: false }, { category: 'term', isChallenge: false },
    { category: 'fact', isChallenge: false }, { category: 'relation', isChallenge: true },
  ]), '용어 2 · 사실 1 · 관계(도전) 1');
});

test('first-attempt summary skips unanswered questions and separates challenge', () => {
  const summary = firstAttemptSummary({ questions: [q('term', false, true), q('term', false, false), q('relation', true, true), q('fact', false, null)] });
  assert.equal(summary.answered, 3);
  assert.deepEqual(summary.challenge, { correct: 1, total: 1 });
  assert.equal(categoryResultText(summary), '용어 1/2 · 관계 1/1');
});

test('student list status follows first score and current wrong count', () => {
  const base = { questionCount: 5, submittedQuestionCount: 5, firstCorrectCount: 4, currentWrongCount: 0 };
  assert.equal(progressLabel({ ...base, status: 'not_started', submittedQuestionCount: 0 }), '풀기 전');
  assert.equal(progressLabel({ ...base, status: 'in_progress', submittedQuestionCount: 2 }), '푸는 중 2/5');
  assert.equal(progressLabel({ ...base, status: 'completed', currentWrongCount: 1 }), '다시 풀 문제 1');
  assert.equal(progressLabel({ ...base, status: 'completed' }), '완료 4/5');
});

test('resume from the first question without a first answer', () => {
  assert.equal(nextUnanswered(['a', 'b', 'c'], new Set(['a', 'c'])), 1);
  assert.equal(nextUnanswered(['a'], new Set(['a'])), null);
});

test('level tabs follow diagnostic order', () => {
  const groups = groupByLevel([{ generation: { baseLevel: 'advanced' } }, { generation: null }, { generation: { baseLevel: 'beginner' } }]);
  assert.deepEqual(groups.map((g) => g.key), ['beginner', 'advanced', 'other']);
});
