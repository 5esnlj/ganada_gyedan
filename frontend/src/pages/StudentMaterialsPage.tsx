import { useState } from 'react';
import { Download } from 'lucide-react';
import { SUBJECT_LABELS } from '../api';
import { dateLabel } from '../api/adapters';
import { pdfMakerApi } from '../api/pdfMaker';
import type { AreaCode } from '../api/wire';
import { Button, FolderCard, PageHeader } from '../components';
import { useApi } from '../hooks';
import styles from './StudentMaterialsPage.module.css';

function LoadError({ message, retry }: { message: string; retry: () => void }) {
  return <div role="alert"><p>{message}</p><Button variant="secondary" onClick={retry}>다시 불러오기</Button></div>;
}

export function StudentMaterialsPage({ classId, onOpen }: { classId: string; onOpen: (id: string) => void }) {
  const [subject, setSubject] = useState<AreaCode | ''>('');
  const { data, error, retry } = useApi(() => pdfMakerApi.editions(classId, subject || undefined), [classId, subject]);
  return <>
    <PageHeader title="받은 자료" actions={<Button variant="secondary" onClick={retry}>새로고침</Button>} />
    <label className={styles.filter}>교과
      <select value={subject} onChange={(event) => setSubject(event.target.value as AreaCode | '')}>
        <option value="">전체 교과</option>
        {Object.entries(SUBJECT_LABELS).map(([code, label]) => <option key={code} value={code}>{label}</option>)}
      </select>
    </label>
    {error ? <LoadError message={error.message} retry={retry} />
      : !data ? <p role="status">받은 자료를 불러오는 중…</p>
      : data.items.length ? <div className={styles.cards}>{data.items.map((material) => (
        <FolderCard key={material.id} subject={SUBJECT_LABELS[material.subject]} title={material.title}
          meta={[material.unit, dateLabel(material.publishedAt)].filter(Boolean).join(' · ')}
          badge={material.isRecentPublished ? { text: 'NEW', strong: true } : undefined}
          onClick={() => onOpen(material.id)} />
      ))}</div> : <p>아직 {subject ? '이 교과에서 ' : ''}받은 자료가 없습니다.</p>}
  </>;
}

export function StudentMaterialView({ id, onBack }: { id: string; onBack: () => void }) {
  const { data, error, retry } = useApi(() => pdfMakerApi.edition(id), [id]);
  const back = <Button variant="secondary" onClick={onBack}>받은 자료 목록</Button>;
  if (error) return <>{back}<LoadError message={error.message} retry={retry} /></>;
  if (!data) return <>{back}<p role="status">자료를 불러오는 중…</p></>;
  const { content, ...material } = data.data;
  const notes = new Map(content.studyNotes?.map((note) => [note.blockId, note]));
  const hidden = new Set(content.hiddenBlockIds);
  const grouped = new Map<number | null, typeof content.paragraphs>();
  for (const paragraph of content.paragraphs) {
    if (hidden.has(paragraph.id)) continue;
    const page = paragraph.page ?? null;
    const group = grouped.get(page) ?? [];
    group.push(paragraph);
    grouped.set(page, group);
  }
  return <>
    <PageHeader title={material.title} actions={back} />
    <div className={styles.meta}>
      <span>{[SUBJECT_LABELS[material.subject], material.unit, dateLabel(material.publishedAt)].filter(Boolean).join(' · ')}</span>
      {material.hasPdf && <a className={styles.download} href={pdfMakerApi.downloadUrl(id)}>
        <Download size={18} aria-hidden="true" /> PDF 내려받기
      </a>}
    </div>
    {[...grouped].map(([page, paragraphs]) => <section key={page ?? 'body'} className={styles.reader}>
      <h2>{page ? `${page}쪽` : '본문'}</h2>
      {paragraphs.map((paragraph) => {
        const note = notes.get(paragraph.id);
        const visual = note?.visual;
        return <div key={paragraph.id}>
          <p>{paragraph.text}</p>
          {(note?.text || visual) && <details>
            <summary>읽기 도움말</summary>
            {note?.text && <p>{note.text}</p>}
            {visual && <>
              {visual.title && <strong>{visual.title}</strong>}
              {visual.center && <p>{visual.center}</p>}
              {visual.kind === 'seq' ? <ol>{visual.items.map((item, index) => <li key={index}>{item}</li>)}</ol>
                : <ul>{visual.items.map((item, index) => <li key={index}>{item}</li>)}</ul>}
            </>}
          </details>}
        </div>;
      })}
    </section>)}
    {content.translations.length > 0 && <section className={styles.reader}>
      <h2>핵심어와 모국어 뜻</h2>
      <dl>{content.translations.map(({ term, meaning, translation }, index) => (
        <div key={`${term}:${index}`}>
          <dt>{term}</dt>
          <dd>{[meaning, translation].filter(Boolean).join(' · ')}</dd>
        </div>
      ))}</dl>
    </section>}
  </>;
}
