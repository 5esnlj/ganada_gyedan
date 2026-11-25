import { useRef, useState, type FormEvent } from 'react';
import { Download } from 'lucide-react';
import { ApiError } from '../api';
import type { MaterialContent } from '../api/pdfMaker';
import { Button } from '../components';
import { useApi } from '../hooks';
import { canEditPdfBlock, exportPreviewPdf, renderConvertedPages, type PdfBlock, type PdfPage } from '../pdf';
import { revertPhrase } from '../pdf/phrases';
import styles from './PdfMakerPage.module.css';
import { errorMessage, Failure, Panel, useAction } from './teacherWorkspace';

type Paragraph = MaterialContent['paragraphs'][number];

function pageNumber(paragraph: Paragraph, firstPage: number) {
  const idPage = /^p([1-9][0-9]*)b/.exec(paragraph.id);
  return paragraph.page ?? (idPage ? Number(idPage[1]) : firstPage);
}

function previewContent(content: MaterialContent, firstPage: number) {
  return {
    ...content,
    paragraphs: content.paragraphs.map((paragraph) => ({ ...paragraph, page: pageNumber(paragraph, firstPage) })),
  };
}

function renderPage(page: PdfPage, content: MaterialContent, firstPage: number) {
  const prepared = previewContent(content, firstPage);
  const pageIds = new Set(page.blocks.map((block) => block.id));
  return renderConvertedPages([page], {
    ...prepared,
    hiddenBlockIds: (content.hiddenBlockIds ?? []).filter((id) => pageIds.has(id) || id.startsWith(`p${page.number}b`)),
  });
}

export function hasPositionedParagraphs(pages: PdfPage[], content: MaterialContent) {
  if (!pages.length) return false;
  const paragraphs = new Map(previewContent(content, pages[0].number).paragraphs.map((p) => [p.id, p.page]));
  return pages.some((page) => page.blocks.some((block) => paragraphs.get(block.id) === page.number));
}

export async function makeEditionPdf(originals: PdfPage[], content: MaterialContent) {
  const prepared = previewContent(content, originals[0].number);
  const full = await renderConvertedPages(originals, prepared);
  return exportPreviewPdf(full, prepared);
}

function bounds(page: PdfPage, block: PdfBlock) {
  const left = Math.max(0, block.x);
  const top = Math.max(0, block.y);
  const right = Math.min(page.width, block.x + block.width);
  const bottom = Math.min(page.height, block.y + Math.max(block.height, block.fontSize));
  return {
    left: `${left / page.width * 100}%`,
    top: `${top / page.height * 100}%`,
    width: `${Math.max(0, right - left) / page.width * 100}%`,
    height: `${Math.max(0, bottom - top) / page.height * 100}%`,
  };
}

function lineTargets(block: PdfBlock) {
  const width = block.width || 1;
  const height = Math.max(block.height, block.fontSize) || 1;
  const lines = block.lines.length ? block.lines : [[block.x, block.y, width, height]];
  return lines.map(([x, y, lineWidth, size], index) => (
    <span key={index} className={styles.paragraphLine} aria-hidden="true" style={{
      left: `${(x - block.x) / width * 100}%`,
      top: `${(y - block.y) / height * 100}%`,
      width: `${lineWidth / width * 100}%`,
      height: `${size / height * 100}%`,
    }} />
  ));
}

function ParagraphEditor({ block, page, paragraph, content, firstPage, onSave, onClose, onReload }: {
  block: PdfBlock;
  page: PdfPage;
  paragraph: Paragraph;
  content: MaterialContent;
  firstPage: number;
  onSave: (content: MaterialContent) => Promise<void>;
  onClose: () => void;
  onReload: () => void;
}) {
  const [draft, setDraft] = useState(paragraph);
  const { busy, error, run } = useAction();
  const below = block.y + block.height < page.height * 0.57;
  const y = below ? block.y + block.height : block.y;
  const source = block.text.replace(/‸/g, ' ').trim();
  const changes = draft.phraseChanges ?? [];
  const terms = content.translations.filter(({ term }) => term && draft.text.includes(term));
  const canSave = draft.text.trim().length > 0 && draft.text.trim() !== paragraph.text.trim();

  function setText(text: string) {
    setDraft({ ...paragraph, text, phraseChanges: text === paragraph.text ? paragraph.phraseChanges : [] });
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSave || busy) return;
    void run(async () => {
      const updated = draft.phraseChanges?.length ? draft : { ...draft, text: draft.text.trim() };
      const next = {
        ...content,
        paragraphs: content.paragraphs.map((item) => item.id === paragraph.id ? updated : item),
      };
      const candidate = await renderPage(page, next, firstPage);
      if (candidate.unplacedBlockIds.includes(block.id)) {
        throw new Error('수정한 문장이 지면에 들어가지 않습니다. 글을 줄이거나 원문을 가져와 주세요.');
      }
      await onSave(next);
      onClose();
    });
  }

  return (
    <form
      className={styles.inlineEditor}
      data-place={below ? 'below' : 'above'}
      style={{ top: `${Math.min(100, Math.max(0, y / page.height * 100))}%` }}
      aria-label={`${page.number}쪽 문단 편집`}
      onSubmit={submit}
      onKeyDown={(event) => { if (event.key === 'Escape' && !busy) { event.stopPropagation(); onClose(); } }}
    >
      <label>
        <strong>문단 편집</strong>
        <textarea value={draft.text} onChange={(event) => setText(event.target.value)} maxLength={10000}
          rows={4} autoFocus disabled={busy} />
      </label>
      {changes.length > 0 && (
        <details className={styles.details}>
          <summary>문단의 변환 표현 {changes.length}개</summary>
          <ul className={styles.phraseList}>
            {changes.map((change) => (
              <li key={change.start}>
                <span>{change.original} → <strong>{change.replacement}</strong></span>
                <Button variant="secondary" disabled={busy} aria-label={`${change.original} 원문으로 되돌리기`}
                  onClick={() => setDraft((current) => revertPhrase(current, change))}>되돌리기</Button>
              </li>
            ))}
          </ul>
        </details>
      )}
      {terms.length > 0 && (
        <details className={styles.details}>
          <summary>이 문단의 핵심어 {terms.length}개</summary>
          <ul className={styles.phraseList}>
            {terms.map(({ term, meaning, translation }, index) => (
              <li key={`${term}:${index}`}><strong>{term}</strong><span>
                {[meaning, translation].filter(Boolean).join(' · ')}
              </span></li>
            ))}
          </ul>
        </details>
      )}
      {Boolean(error) && <Failure message={errorMessage(error)}
        retry={error instanceof ApiError && error.status === 412 ? onReload : undefined} />}
      <div className={styles.actions}>
        <Button variant="secondary" onClick={() => setText(source)} disabled={busy || draft.text === source}>원문 가져오기</Button>
        <Button variant="secondary" onClick={onClose} disabled={busy}>취소</Button>
        <Button type="submit" disabled={busy || !canSave}>{busy ? '저장 중…' : '수정 저장'}</Button>
      </div>
    </form>
  );
}

export function PdfComparison({ originals, content, canEdit, canDownload, title, onEditingChange, onSave, onReload }: {
  originals: PdfPage[];
  content: MaterialContent;
  canEdit: boolean;
  canDownload: boolean;
  title: string;
  onEditingChange: (editing: boolean) => void;
  onSave: (content: MaterialContent) => Promise<void>;
  onReload: () => void;
}) {
  const firstPage = originals[0].number;
  const [current, setCurrent] = useState(firstPage);
  const [selected, setSelected] = useState<string | null>(null);
  const anchor = useRef<HTMLButtonElement | null>(null);
  const original = originals.find((page) => page.number === current) ?? originals[0];
  const { data: preview, error: previewError, retry } = useApi(() => renderPage(original, content, firstPage), [original, content, firstPage]);
  const converted = preview?.pages.find((page) => page.number === original.number);
  const phrases = preview?.phrases ?? [];
  const appliedCount = phrases.filter((phrase) => phrase.applied).length;
  const paragraphById = new Map(content.paragraphs
    .filter((paragraph) => pageNumber(paragraph, firstPage) === original.number)
    .map((paragraph) => [paragraph.id, paragraph]));
  const editableBlocks = original.blocks.filter((block) => paragraphById.has(block.id) && canEditPdfBlock(original, block, content));
  const active = editableBlocks.find((block) => block.id === selected);
  const activeParagraph = active && paragraphById.get(active.id);
  const { busy, error, run } = useAction();

  function closeEditor() {
    setSelected(null);
    onEditingChange(false);
    requestAnimationFrame(() => anchor.current?.focus({ preventScroll: true }));
  }

  function download() {
    void run(async () => {
      const url = URL.createObjectURL(await makeEditionPdf(originals, content));
      const link = document.createElement('a');
      link.href = url;
      link.download = `${title}-전환자료.pdf`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  }

  return (
    <Panel title="원본과 전환본 비교">
      <div className={styles.row}>
        <label>미리보기 쪽 <select value={original.number} disabled={selected !== null}
          onChange={(event) => setCurrent(Number(event.target.value))}>
          {originals.map((page) => <option key={page.number} value={page.number}>{page.number}쪽</option>)}
        </select></label>
        <Button variant="secondary" icon={<Download size={18} />} disabled={!canDownload || !converted || busy}
          onClick={download} title={!canDownload ? '전환 자료를 검토한 뒤 PDF를 저장할 수 있습니다.' : undefined}>
          {busy ? 'PDF 만드는 중…' : '전환본 PDF 내려받기'}
        </Button>
      </div>
      {canEdit && <p className={styles.muted}>{editableBlocks.length
        ? '전환본에서 문단을 누르면 그 자리에서 수정할 수 있습니다.'
        : '이 쪽에는 지면에서 수정할 수 있는 문단이 없습니다.'}</p>}
      <div className={styles.compare}>
        <figure>
          <figcaption>원본 {original.number}쪽</figcaption>
          <img src={original.imageUrl} alt={`원본 PDF ${original.number}쪽`} />
        </figure>
        <figure>
          <figcaption>전환본 {original.number}쪽</figcaption>
          <div className={styles.editablePage}>
            {converted ? <img src={converted.imageUrl} alt={`전환 PDF ${original.number}쪽`} />
              : previewError ? <Failure message={previewError.message} retry={retry} />
                : <p role="status">미리보기를 만드는 중…</p>}
            {canEdit && converted && editableBlocks.map((block) => (
              <button key={block.id} type="button" className={styles.paragraphTarget}
                style={bounds(original, block)} aria-expanded={selected === block.id}
                aria-label={`문단 수정 · ${paragraphById.get(block.id)!.text.slice(0, 48)}`}
                title="문단 수정" disabled={selected !== null && selected !== block.id}
                onClick={(event) => {
                  if (selected !== null) return;
                  anchor.current = event.currentTarget;
                  setSelected(block.id);
                  onEditingChange(true);
                }}>{lineTargets(block)}</button>
            ))}
            {active && activeParagraph && <ParagraphEditor key={active.id} block={active} page={original}
              paragraph={activeParagraph} content={content} firstPage={firstPage} onSave={onSave}
              onClose={closeEditor} onReload={onReload} />}
          </div>
        </figure>
      </div>
      {converted && phrases.length > 0 && (
        <details key={original.number} className={styles.details}>
          <summary>지면에 반영된 표현 {appliedCount}개{phrases.length > appliedCount
            ? ` · 도움말 ${phrases.length - appliedCount}개` : ''}</summary>
          <ul className={styles.phraseList}>
            {phrases.map(({ blockId, change, applied }) => (
              <li key={`${blockId}:${change.start}`}>
                <span>{change.original} → <strong>{change.replacement}</strong></span>
                <small>{applied ? '지면 반영' : 'PDF 도움말'}</small>
              </li>
            ))}
          </ul>
        </details>
      )}
      {converted && preview?.notices.length ? <ul className={styles.notices}>
        {preview.notices.map((notice) => <li key={notice}>{notice}</li>)}
      </ul> : null}
      {Boolean(error) && <Failure message={errorMessage(error)} />}
    </Panel>
  );
}
