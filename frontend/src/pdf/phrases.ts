import type { MaterialParagraph, PhraseChange } from '../api/wire';

export interface PhrasePlacement {
  // Canvas line slices use JavaScript string offsets (UTF-16).
  start: number;
  end: number;
}

export function placePhrases(text: string, changes: readonly PhraseChange[], deferred: ReadonlySet<PhraseChange>) {
  const placements: PhrasePlacement[] = [];
  if (!changes.length) return { text, placements };

  const chars = Array.from(text);
  let result = '';
  let cursor = 0;
  for (const change of changes) {
    result += chars.slice(cursor, change.start).join('');
    const start = result.length;
    if (deferred.has(change)) {
      result += change.original;
    } else {
      result += change.replacement;
      placements.push({ start, end: result.length });
    }
    cursor = change.start + Array.from(change.replacement).length;
  }
  return { text: result + chars.slice(cursor).join(''), placements };
}

/** Try to keep more edits within a bounded number of layout attempts. */
export function fitPhraseSubset<T>(
  widestFirst: readonly PhraseChange[],
  fit: (deferred: ReadonlySet<PhraseChange>) => T | null,
): { value: T; deferred: ReadonlySet<PhraseChange> } | null {
  const full = fit(new Set());
  if (full !== null) return { value: full, deferred: new Set() };
  if (widestFirst.length < 2) return null;

  // A wide edit can occur on a roomy line while a shorter edit elsewhere causes the collision.
  for (const change of widestFirst) {
    const deferred = new Set([change]);
    const value = fit(deferred);
    if (value !== null) return { value, deferred };
  }
  if (widestFirst.length === 2) return null;

  const deferred = new Set(widestFirst.slice(0, 1));
  for (const change of widestFirst.slice(1, -1)) {
    deferred.add(change);
    let value = fit(deferred);
    if (value === null) continue;
    for (const earlier of deferred) {
      const candidate = new Set(deferred);
      candidate.delete(earlier);
      const restored = fit(candidate);
      if (restored === null) continue;
      value = restored;
      deferred.delete(earlier);
    }
    return { value, deferred };
  }

  // If the width order found no combination, try each remaining single edit.
  for (const keep of widestFirst.slice(0, -1).reverse()) {
    const single = new Set(widestFirst.filter(change => change !== keep));
    const value = fit(single);
    if (value !== null) return { value, deferred: single };
  }
  return null;
}

export function revertPhrase(paragraph: MaterialParagraph, target: PhraseChange): MaterialParagraph {
  const changes = paragraph.phraseChanges ?? [];
  if (!changes.includes(target)) return paragraph;
  const { text } = placePhrases(paragraph.text, changes, new Set([target]));
  const delta = Array.from(target.original).length - Array.from(target.replacement).length;
  const phraseChanges = changes.filter((change) => change !== target).map((change) =>
    change.start > target.start ? { ...change, start: change.start + delta } : change);
  return { ...paragraph, text, phraseChanges };
}
