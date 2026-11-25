import { useEffect, useRef, useState, type FormEvent } from 'react';
import { FileText } from 'lucide-react';
import { ApiError, SUBJECT_LABELS } from '../api';
import {
  pdfMakerApi, type GenerationJob, type MaterialContent, type PdfSourceOut,
  type StudentEdition, type StudentTarget,
} from '../api/pdfMaker';
import type { AreaCode } from '../api/wire';
import { Button, LevelPill, MaterialCard, PageHeader, UploadBox } from '../components';
import { useApi } from '../hooks';
import {
  openSourcePdf, toConversionPages, type PdfSource,
} from '../pdf';
import { hasPositionedParagraphs, makeEditionPdf, PdfComparison, verifyEditionLayout } from './PdfComparison';
import styles from './PdfMakerPage.module.css';
import { errorMessage, Failure, languageLabel, LEVEL_LABELS, Panel, SolarKey, useAction } from './teacherWorkspace';

const date = new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', year: 'numeric', month: 'short', day: 'numeric' });
const EDITION_LABELS: Record<StudentEdition['status'], string> = {
  draft: '초안', reviewed: '검토 완료', published: '배포됨', withdrawn: '회수됨',
};
const JOB_LABELS: Record<GenerationJob['status'], string> = {
  queued: '대기 중', running: '변환 중', succeeded: '완료', failed: '실패', unknown: '상태 확인 필요',
};
function SourceUpload({ classId, onCreated, onCancel }: {
  classId: string;
  onCreated: (source: PdfSourceOut) => void;
  onCancel: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState('');
  const [unit, setUnit] = useState('');
  const [fileError, setFileError] = useState('');
  const { busy, error, run } = useAction();

  function select(files: File[]) {
    if (busy || files.length === 0) return;
    const [selected] = files;
    if (files.length > 1 || !selected.name.toLowerCase().endsWith('.pdf')) {
      setFileError('한 번에 PDF 파일 한 개를 선택해 주세요.');
      return;
    }
    if (selected.size > 10 * 1024 * 1024) {
      setFileError('PDF는 10 MiB 이하여야 합니다.');
      return;
    }
    setFileError('');
    setFile(selected.type ? selected : new File([selected], selected.name, { type: 'application/pdf' }));
    if (!title) setTitle(selected.name.replace(/\.pdf$/i, '').slice(0, 200));
  }

  function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (file && title.trim()) void run(async () => onCreated(await pdfMakerApi.uploadSource(classId, file, title.trim(), unit.trim())));
  }

  return (
    <Panel title="원본 PDF 등록">
      <form className={styles.form} onSubmit={upload}>
        <UploadBox onFiles={select} />
        <p role="status" className={styles.muted}>{file ? file.name : '텍스트가 있는 PDF를 올려 주세요. 최대 10 MiB입니다.'}</p>
        <label>자료 제목<input value={title} onChange={(event) => setTitle(event.target.value)} required maxLength={200} /></label>
        <label>단원 이름<input value={unit} onChange={(event) => setUnit(event.target.value)} maxLength={150} /></label>
        {fileError && <Failure message={fileError} />}
        {Boolean(error) && <Failure message={errorMessage(error)} />}
        <div className={styles.actions}>
          <Button variant="secondary" onClick={onCancel} disabled={busy}>취소</Button>
          <Button type="submit" disabled={busy || !file || !title.trim()}>{busy ? '등록 중…' : '원본 등록'}</Button>
        </div>
      </form>
    </Panel>
  );
}

function SourceList({ classId, subject, onSelect }: {
  classId: string;
  subject: AreaCode;
  onSelect: (source: PdfSourceOut) => void;
}) {
  const { data, error, retry } = useApi(() => pdfMakerApi.sources(classId, subject), [classId, subject]);
  return (
    <section className={styles.section}>
      <h2>{SUBJECT_LABELS[subject]} · 내 교과</h2>
      {error ? <Failure message={error.message} retry={retry} /> : !data
        ? <p role="status">원본 자료를 불러오는 중…</p>
        : data.items.length ? (
          <div className={styles.cards}>
            {data.items.map((source) => (
              <MaterialCard
                key={source.id} subject={SUBJECT_LABELS[source.subject]} unit={source.unit}
                pageTitle={source.title} name={source.title} date={date.format(new Date(source.createdAt))}
                onClick={() => onSelect(source)}
              />
            ))}
          </div>
        ) : <p className={styles.muted}>등록된 원본 자료가 없습니다.</p>}
    </section>
  );
}

function useSourcePdf(fileId: string | null) {
  const [pdf, setPdf] = useState<PdfSource | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let mounted = true;
    let opened: PdfSource | null = null;
    setPdf(null);
    setError(null);
    if (fileId) {
      void pdfMakerApi.sourcePdf(fileId).then(openSourcePdf).then((document) => {
        if (!mounted) return document.close();
        opened = document;
        setPdf(document);
      }).catch((cause: unknown) => {
        if (mounted) setError(errorMessage(cause));
      });
    }
    return () => {
      mounted = false;
      if (opened) void opened.close().catch(() => undefined);
    };
  }, [fileId, revision]);

  return { pdf, error, retry: () => setRevision((value) => value + 1) };
}

function SourceSettings({ classId, source, pdf, targets, onQueued }: {
  classId: string;
  source: PdfSourceOut;
  pdf: PdfSource;
  targets: StudentTarget[];
  onQueued: () => void;
}) {
  const [pageFrom, setPageFrom] = useState('1');
  const [pageTo, setPageTo] = useState(String(Math.min(pdf.pageCount, 4)));
  const [selected, setSelected] = useState<string[]>([]);
  const [progress, setProgress] = useState('');
  const [keyReady, setKeyReady] = useState(false);
  const { busy, error, run } = useAction();
  const priorRequest = useRef<{ body: string; key: string }>();
  const from = Number(pageFrom);
  const to = Number(pageTo);
  const validRange = Number.isInteger(from) && Number.isInteger(to)
    && from >= 1 && to >= from && to <= pdf.pageCount && to - from < 10;

  function toggle(id: string) {
    setSelected((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!keyReady || !validRange || selected.length === 0) return;
    void run(async () => {
      setProgress('PDF의 글과 배치를 읽는 중…');
      try {
        const pageData = await pdf.readPages(from, to, (completed, total) => {
          setProgress(`원본 분석 ${completed} / ${total}쪽`);
        });
        const pages = toConversionPages(pageData);
        if (!pages.some((page) => page.blocks.length)) throw new Error('선택한 쪽에서 읽을 수 있는 글을 찾지 못했습니다.');
        const members = [...selected].sort();
        const body = JSON.stringify([source.id, members, pages]);
        if (priorRequest.current?.body !== body) {
          priorRequest.current = { body, key: crypto.randomUUID() };
        }
        setProgress('학생별 변환을 요청하는 중…');
        await pdfMakerApi.generate(classId, source.id, members, pages, priorRequest.current.key);
        priorRequest.current = undefined;
        onQueued();
      } finally {
        setProgress('');
      }
    });
  }

  return (
    <>
      <SolarKey onReady={setKeyReady} />
      <Panel title="학생별 자료 만들기">
        <form className={styles.form} onSubmit={submit}>
          <fieldset className={styles.range} disabled={busy}>
            <legend>변환할 쪽 · 전체 {pdf.pageCount}쪽</legend>
            <label>시작 쪽<input type="number" min={1} max={pdf.pageCount} value={pageFrom}
              onChange={(event) => setPageFrom(event.target.value)} required /></label>
            <label>마지막 쪽<input type="number" min={1} max={pdf.pageCount} value={pageTo}
              onChange={(event) => setPageTo(event.target.value)} required /></label>
          </fieldset>
          <p className={styles.muted}>{validRange
            ? '한 번에 연속된 최대 10쪽을 변환할 수 있습니다.'
            : 'PDF 범위 안에서 시작 쪽부터 연속된 1~10쪽을 선택해 주세요.'}</p>
          <fieldset className={styles.targets} disabled={busy}>
            <legend>대상 학생 · {selected.length}명 선택</legend>
            {!targets.length && <p>클래스에 등록된 학생이 없습니다.</p>}
            {targets.map((student) => {
              const diagnosed = student.diagnosticStatus === 'completed';
              const subjectLevel = student.areaResults.find((area) => area.area === source.subject)?.level;
              const readingLevel = student.partResults.find((part) => part.area === source.subject && part.skill === 'reading')?.level;
              const level = subjectLevel ?? readingLevel;
              const checked = selected.includes(student.id);
              return (
                <label key={student.id} className={styles.target}>
                  <input type="checkbox" checked={checked} disabled={!diagnosed || !level || (!checked && selected.length >= 30)}
                    onChange={() => toggle(student.id)} />
                  <span>{student.displayName} <small>· {languageLabel(student.nativeLanguage)}</small></span>
                  {diagnosed && level ? <span className={styles.reading}>{subjectLevel ? SUBJECT_LABELS[source.subject] : '읽기'} <LevelPill level={LEVEL_LABELS[level]} /></span>
                    : <small>{diagnosed ? '담당 교과 진단 결과 없음' : '진단 미완료'}</small>}
                </label>
              );
            })}
          </fieldset>
          {Boolean(error) && <Failure message={errorMessage(error)} />}
          {progress && <p role="status">{progress}</p>}
          {!keyReady && <p className={styles.muted}>Solar 키를 저장한 뒤 변환을 시작할 수 있습니다.</p>}
          <div className={styles.actions}>
            <Button type="submit" disabled={!keyReady || busy || !validRange || !selected.length}>
              {busy ? '요청 중…' : '변환 시작'}
            </Button>
          </div>
        </form>
      </Panel>
    </>
  );
}

function useJobs(classId: string, sourceId: string) {
  const [jobs, setJobs] = useState<GenerationJob[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let mounted = true;
    let timer: number | undefined;
    async function load() {
      try {
        const result = await pdfMakerApi.jobs(classId);
        if (!mounted) return;
        const matching = result.items.filter((item) => item.sourceId === sourceId);
        setJobs(matching);
        setError(null);
        if (matching.some((item) => item.status === 'queued' || item.status === 'running')) {
          timer = window.setTimeout(load, 1200);
        }
      } catch (cause) {
        if (mounted) setError(errorMessage(cause));
      }
    }
    void load();
    return () => {
      mounted = false;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [classId, sourceId, revision]);

  return { jobs, error, retry: () => setRevision((value) => value + 1) };
}

function SourceWorkspace({ classId, source, onReview }: {
  classId: string;
  source: PdfSourceOut;
  onReview: (id: string) => void;
}) {
  const original = useSourcePdf(source.fileId);
  const jobs = useJobs(classId, source.id);
  const roster = useApi(() => pdfMakerApi.students(classId), [classId]);
  const editions = useApi(() => pdfMakerApi.editions(classId), [classId]);
  const products = editions.data?.items.filter((item) => item.sourceId === source.id) ?? [];
  const studentName = (id: string) => roster.data?.items.find((row) => row.id === id)?.displayName ?? id;

  return (
    <>
      <details className={styles.details}>
        <summary><FileText size={18} aria-hidden="true" /> 원본 추출 텍스트</summary>
        <p>{source.sourceText || '추출한 텍스트가 없습니다.'}</p>
      </details>
      {!source.fileId ? <Failure message="변환할 원본 PDF가 등록되지 않았습니다." /> : original.error
        ? <Failure message={original.error} retry={original.retry} />
        : roster.error ? <Failure message={roster.error.message} retry={roster.retry} />
        : original.pdf && roster.data ? (
          <SourceSettings classId={classId} source={source} pdf={original.pdf} targets={roster.data.items} onQueued={jobs.retry} />
        ) : <p role="status">원본과 학생 목록을 불러오는 중…</p>}
      <Panel title="변환 작업">
        {jobs.error ? <Failure message={jobs.error} retry={jobs.retry} /> : !jobs.jobs
          ? <p role="status">작업 목록을 불러오는 중…</p>
          : jobs.jobs.length ? (
            <ul className={styles.records}>
              {jobs.jobs.map((job) => {
                const { resultId } = job;
                return (
                  <li key={job.id}>
                    <div>
                      <strong>{studentName(job.studentMemberId)} · {JOB_LABELS[job.status]}</strong>
                      {job.errorMessage && <p>{job.errorMessage}</p>}
                    </div>
                    {job.status === 'succeeded' && resultId && (
                      <Button variant="secondary" onClick={() => onReview(resultId)}>결과 검토</Button>
                    )}
                  </li>
                );
              })}
            </ul>
          ) : <p className={styles.muted}>아직 요청한 작업이 없습니다.</p>}
      </Panel>
      <Panel title="저장된 학생별 자료">
        {editions.error ? <Failure message={editions.error.message} retry={editions.retry} /> : !editions.data
          ? <p role="status">자료를 불러오는 중…</p>
          : products.length ? (
            <ul className={styles.records}>
              {products.map((item) => (
                <li key={item.id}>
                  <span>{studentName(item.studentMemberId)} · {EDITION_LABELS[item.status]} · 버전 {item.revision}</span>
                  <Button variant="secondary" onClick={() => onReview(item.id)}>자료 열기</Button>
                </li>
              ))}
            </ul>
          ) : <p className={styles.muted}>저장된 전환 자료가 없습니다.</p>}
      </Panel>
    </>
  );
}

function useReviewPages(pdf: PdfSource | null, content: MaterialContent) {
  const hiddenPages = content.hiddenBlockIds?.flatMap((id) => {
    const match = /^p([1-9][0-9]*)b/.exec(id);
    return match ? [Number(match[1])] : [];
  }) ?? [];
  const pagesInContent = [
    ...content.paragraphs.flatMap((paragraph) => paragraph.page == null ? [] : [paragraph.page]),
    ...content.omittedPages, ...hiddenPages,
  ];
  const from = pagesInContent.length ? Math.min(...pagesInContent) : 1;
  const to = pagesInContent.length ? Math.max(...pagesInContent) : 1;
  return useApi(async () => {
    if (!pdf) return [];
    if (from < 1 || to > pdf.pageCount) {
      throw new Error('초안의 쪽 번호가 원본 PDF와 맞지 않습니다. 원본으로 돌아가 다시 변환해 주세요.');
    }
    return pdf.readPages(from, to);
  }, [pdf, from, to]);
}

function EditionEditor({ source, initial, initialVersion, onReload }: {
  source: PdfSourceOut;
  initial: StudentEdition;
  initialVersion: string;
  onReload: () => void;
}) {
  const [edition, setEdition] = useState(initial);
  const [version, setVersion] = useState(initialVersion);
  const [edited, setEdited] = useState<MaterialContent>(initial.content);
  const [editingOnPage, setEditingOnPage] = useState(false);
  const original = useSourcePdf(source.fileId);
  const pages = useReviewPages(original.pdf, edition.content);
  const { busy, error, run } = useAction();
  const dirty = edited.paragraphs.some((paragraph, index) => paragraph.text !== edition.content.paragraphs[index].text);
  const editable = edition.status === 'draft' || edition.status === 'reviewed';
  const allTextValid = edited.paragraphs.every((paragraph) => paragraph.text.trim());
  const positioned = pages.data ? hasPositionedParagraphs(pages.data, edition.content) : null;
  const requiresPdf = Boolean(source.fileId || edition.content.hiddenBlockIds?.length);
  const checkingLayout = requiresPdf && (!pages.data?.length || !original.pdf);
  const showTextEditor = !source.fileId || Boolean(original.error || pages.error)
    || positioned === false;

  function changeParagraph(id: string, text: string) {
    setEdited((content) => ({
      ...content,
      paragraphs: content.paragraphs.map((p) => p.id === id ? { ...p, text, phraseChanges: [] } : p),
    }));
  }

  async function saveContent(content: MaterialContent) {
    const result = await pdfMakerApi.edit(edition.id, content, version);
    accept(result);
  }

  function accept(result: Awaited<ReturnType<typeof pdfMakerApi.edit>>) {
    if (!result.etag) throw new Error('다음 편집에 필요한 자료 버전을 받지 못했습니다.');
    setEdition(result.data);
    setVersion(result.etag);
    setEdited(result.data.content);
  }

  function update(action: 'review' | 'publish') {
    void run(async () => {
      const mustRender = positioned || Boolean(edition.content.hiddenBlockIds?.length);
      if (mustRender) {
        if (!pages.data?.length) throw new Error('원본 지면을 확인할 수 없습니다. 원본으로 돌아가 다시 변환해 주세요.');
        if (action === 'publish') {
          const pdf = await makeEditionPdf(pages.data, edition.content);
          accept(await pdfMakerApi.publishPdf(edition.id, pdf, version));
          return;
        }
        await verifyEditionLayout(pages.data, edition.content);
      }
      accept(await pdfMakerApi.transition(edition.id, action, version));
    });
  }

  return (
    <>
      <p className={styles.status}><strong>{EDITION_LABELS[edition.status]}</strong> · 버전 {edition.revision}</p>
      {!source.fileId ? edition.content.hiddenBlockIds?.length
        ? <Failure message="원본 PDF가 없어 교사용 문단을 안전하게 가렸는지 확인할 수 없습니다." />
        : <p className={styles.muted}>원본 PDF가 없어 본문으로 검토할 수 있습니다.</p>
        : original.error ? <Failure message={original.error} retry={original.retry} />
        : pages.error ? <Failure message={pages.error.message} retry={pages.retry} />
        : pages.data?.length ? <PdfComparison originals={pages.data} content={edition.content} title={edition.title}
          canEdit={editable && !busy} onEditingChange={setEditingOnPage} onSave={saveContent} onReload={onReload}
          canDownload={!dirty && !editingOnPage && !busy && (edition.status === 'reviewed' || edition.status === 'published')} />
        : <p role="status">PDF 원본을 불러오는 중…</p>}
      <details className={styles.details}>
        <summary><FileText size={18} aria-hidden="true" /> 원본 추출 텍스트</summary>
        <p>{edition.originalContent || source.sourceText || '추출한 텍스트가 없습니다.'}</p>
      </details>
      {showTextEditor && <Panel title="전환 본문">
        <div className={styles.form}>
          {edited.paragraphs.map((paragraph, index) => (
            <label key={paragraph.id}>
              {paragraph.page ? `${paragraph.page}쪽 · ` : ''}문단 {index + 1}
              <textarea value={paragraph.text} rows={4} maxLength={10000} readOnly={!editable}
                disabled={busy} onChange={(event) => changeParagraph(paragraph.id, event.target.value)} />
            </label>
          ))}
        </div>
        {editable && <div className={styles.actions}>
          <Button variant="secondary" disabled={busy || !dirty || !allTextValid}
            onClick={() => void run(() => saveContent(edited))}>수정 저장</Button>
        </div>}
      </Panel>}
      {Boolean(error) && <Failure message={errorMessage(error)}
        retry={error instanceof ApiError && error.status === 412 ? onReload : undefined} />}
      {editable && (
        <div className={styles.actions}>
          {edition.status === 'draft' && <Button disabled={busy || dirty || editingOnPage || checkingLayout}
            onClick={() => update('review')}>{busy ? '지면 확인 중…' : '검토 완료'}</Button>}
          {edition.status === 'reviewed' && <Button disabled={busy || dirty || editingOnPage || checkingLayout}
            onClick={() => update('publish')}>{busy ? '배포 중…' : '학생에게 배포'}</Button>}
        </div>
      )}
      {(edited.translations.length > 0 || Boolean(edited.studyNotes?.length)) && (
        <Panel title="학습 보조 내용">
          {edited.translations.length > 0 && (
            <ul className={styles.records}>
              {edited.translations.map((translation, index) => (
                <li key={index}><strong>{translation.term}</strong><span>{[translation.meaning, translation.translation].filter(Boolean).join(' · ')}</span></li>
              ))}
            </ul>
          )}
          {edited.studyNotes?.map(({ blockId, text, visual }) => (
            <div className={styles.note} key={blockId}>
              {text && <p>{text}</p>}
              {visual && <><strong>{visual.title}</strong>{visual.center && <p>{visual.center}</p>}
                {visual.kind === 'seq' ? <ol>{visual.items.map((item, index) => <li key={index}>{item}</li>)}</ol>
                  : <ul>{visual.items.map((item, index) => <li key={index}>{item}</li>)}</ul>}</>}
            </div>
          ))}
        </Panel>
      )}
    </>
  );
}

function EditionReview({ source, materialId }: { source: PdfSourceOut; materialId: string }) {
  const { data, error, retry } = useApi(() => pdfMakerApi.edition(materialId), [materialId]);
  if (error) return <Failure message={error.message} retry={retry} />;
  if (!data) return <p role="status">학생별 자료를 불러오는 중…</p>;
  if (!data.etag) return <Failure message="검토에 필요한 자료 버전을 받지 못했습니다." retry={retry} />;
  return <EditionEditor source={source} initial={data.data} initialVersion={data.etag} onReload={retry} />;
}

export function PdfMakerPage({ classId, subject, openUploadRequest = 0 }: {
  classId: string;
  subject: AreaCode;
  openUploadRequest?: number;
}) {
  const [source, setSource] = useState<PdfSourceOut | null>(null);
  const [uploading, setUploading] = useState(openUploadRequest > 0);
  const [materialId, setMaterialId] = useState<string | null>(null);
  const handledUploadRequest = useRef(openUploadRequest);

  useEffect(() => {
    if (handledUploadRequest.current === openUploadRequest) return;
    handledUploadRequest.current = openUploadRequest;
    if (!openUploadRequest) return;
    setSource(null);
    setMaterialId(null);
    setUploading(true);
  }, [openUploadRequest]);

  function select(selected: PdfSourceOut) { setSource(selected); setUploading(false); }

  return (
    <div className={styles.page}>
      <PageHeader title={materialId ? '자료 검토' : source ? source.title : '자료 제작'} actions={
        materialId ? <Button variant="secondary" onClick={() => setMaterialId(null)}>원본으로</Button>
          : source ? <Button variant="secondary" onClick={() => setSource(null)}>자료 목록</Button>
            : !uploading ? <Button onClick={() => setUploading(true)}>새 자료 만들기</Button> : undefined
      } />
      {source ? materialId
        ? <EditionReview key={materialId} source={source} materialId={materialId} />
        : <SourceWorkspace key={source.id} classId={classId} source={source} onReview={setMaterialId} />
        : <>
          {uploading && <SourceUpload classId={classId} onCreated={select} onCancel={() => setUploading(false)} />}
          <SourceList classId={classId} subject={subject} onSelect={select} />
        </>}
    </div>
  );
}
