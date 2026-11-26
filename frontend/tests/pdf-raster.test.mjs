import assert from 'node:assert/strict';
import test from 'node:test';
import { wrapLine } from '../.test-build/pdf/raster.js';

const context = { measureText: text => ({ width: [...text].length }) };

test('PDF can fill a tight Korean line without forcing a wide ragged margin', () => {
  assert.deepEqual(wrapLine(context, '글을 쉽게 설명합니다', 8), ['글을 쉽게', '설명합니다']);
  assert.deepEqual(wrapLine(context, '글을 쉽게 설명합니다', 8, true), ['글을 쉽게 설명', '합니다']);
  assert.deepEqual(wrapLine(context, '쉬운 설명 읽기', 5, true), ['쉬운 설명', '읽기']);
});

test('PDF wrapping keeps punctuation with adjacent text and leaves joined glyphs intact', () => {
  assert.deepEqual(wrapLine(context, '가다. 라고', 2, true), ['가', '다. 라고']);
  assert.deepEqual(wrapLine(context, '가 (다)', 3, true), ['가', '(다)']);
  assert.deepEqual(wrapLine(context, 'e\u0301설명', 1, true), ['', 'e\u0301설명']);
});
