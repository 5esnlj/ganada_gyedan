import assert from 'node:assert/strict';
import test from 'node:test';
import { fitPhraseSubset, placePhrases, revertPhrase } from '../.test-build/pdf/phrases.js';

const first = { start: 2, original: '살핍니다', replacement: '잘 살펴봅니다' };
const second = { start: 11, original: '질문합니다', replacement: '묻습니다' };
const paragraph = { id: 'p1b1', text: '📘 잘 살펴봅니다. 묻습니다.', phraseChanges: [first, second] };

test('PDF keeps a shorter replacement when a longer one is deferred', () => {
  const plan = placePhrases(paragraph.text, paragraph.phraseChanges, new Set([first]));
  assert.equal(plan.text, '📘 살핍니다. 묻습니다.');
  assert.equal(plan.placements.length, 1);
  const [placed] = plan.placements;
  assert.equal(plan.text.slice(placed.start, placed.end), '묻습니다');
});

test('restoring one phrase updates the remaining code point offsets', () => {
  const restored = revertPhrase(paragraph, first);
  assert.equal(restored.text, '📘 살핍니다. 묻습니다.');
  assert.deepEqual(restored.phraseChanges, [{ ...second, start: 8 }]);
  assert.deepEqual(revertPhrase(restored, restored.phraseChanges[0]), {
    id: 'p1b1', text: '📘 살핍니다. 질문합니다.', phraseChanges: [],
  });
});

test('PDF defers the edit that actually collides and keeps wider edits on clear lines', () => {
  const third = { start: 16, original: '발표', replacement: '이야기' };
  const value = fitPhraseSubset([first, second, third], deferred => deferred.has(second) ? 'fits' : null);
  assert.deepEqual(value, { value: 'fits', deferred: new Set([second]) });
});

test('PDF restores an early width-based deferral after clearing multiple collisions', () => {
  const third = { start: 16, original: '발표', replacement: '이야기' };
  const fourth = { start: 21, original: '완료', replacement: '마무리' };
  const value = fitPhraseSubset([first, second, third, fourth], deferred =>
    deferred.has(second) && deferred.has(third) ? 'fits' : null);
  assert.deepEqual(value, { value: 'fits', deferred: new Set([second, third]) });
});
