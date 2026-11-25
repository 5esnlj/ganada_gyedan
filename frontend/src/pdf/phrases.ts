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

export function revertPhrase(paragraph: MaterialParagraph, target: PhraseChange): MaterialParagraph {
  const changes = paragraph.phraseChanges ?? [];
  if (!changes.includes(target)) return paragraph;
  const { text } = placePhrases(paragraph.text, changes, new Set([target]));
  const delta = Array.from(target.original).length - Array.from(target.replacement).length;
  const phraseChanges = changes.filter((change) => change !== target).map((change) =>
    change.start > target.start ? { ...change, start: change.start + delta } : change);
  return { ...paragraph, text, phraseChanges };
}
