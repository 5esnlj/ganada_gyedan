import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { Download, FileText } from 'lucide-react';
import { ApiError, SUBJECT_LABELS } from '../api';
import {
  pdfMakerApi, type Credential, type GenerationJob, type MaterialContent, type PdfSourceOut,
  type StudentEdition, type StudentTarget,
} from '../api/pdfMaker';
import type { AreaCode, LevelCode } from '../api/wire';
import { Button, LevelPill, MaterialCard, PageHeader, UploadBox } from '../components';
import { useApi } from '../hooks';
import {
  exportPreviewPdf, openSourcePdf, renderConvertedPages, toConversionPages, type PdfPage, type PdfSource,
} from '../pdf';
import type { Level } from '../types';
import styles from './PdfMakerPage.module.css';

const date = new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', year: 'numeric', month: 'short', day: 'numeric' });
const languageNames = new Intl.DisplayNames(['ko'], { type: 'language' });
const EDITION_LABELS: Record<StudentEdition['status'], string> = {
  draft: '초안', reviewed: '검토 완료', published: '배포됨', withdrawn: '회수됨',
};
const JOB_LABELS: Record<GenerationJob['status'], string> = {
  queued: '대기 중', running: '변환 중', succeeded: '완료', failed: '실패', unknown: '상태 확인 필요',
};
const LEVEL_LABELS: Record<LevelCode, Level> = {
  below_beginner: '초급 미달', beginner: '초급', intermediate: '중급', advanced: '고급',
};
const errorMessage = (error: unknown) => error instanceof Error ? error.message : '요청을 처리하지 못했습니다.';

function languageLabel(code: string) {
  if (!code) return '모국어 미등록';
  try { return languageNames.of(code) ?? code; } catch { return code; }
}

function Failure({ message, retry }: { message: string; retry?: () => void }) {
  return (
    <div className={styles.failure} role="alert">
      <p>{message}</p>
      {retry && <Button variant="secondary" onClick={retry}>다시 불러오기</Button>}
    </div>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return <section className={styles.panel}><h2>{title}</h2>{children}</section>;
}

function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const running = useRef(false);

  async function run(action: () => Promise<void>) {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
      running.current = false;
    }
  }

  return { busy, error, run };
}

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

function CredentialForm({ current, onSaved, onCancel }: {
  current?: Credential;
  onSaved: (credential: Credential) => void;
  onCancel: () => void;
}) {
  const keyInput = useRef<HTMLInputElement>(null);
  const { busy, error, run } = useAction();

  function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const apiKey = keyInput.current?.value.trim();
    if (!apiKey) return;
    void run(async () => {
      const credential = await pdfMakerApi.saveCredential(apiKey);
      if (keyInput.current) keyInput.current.value = '';
      onSaved(credential);
    });
  }

  return (
    <form className={styles.form} onSubmit={save}>
      <label>Solar API 키<input ref={keyInput} type="password" autoComplete="off" required maxLength={500} disabled={busy} /></label>
      {Boolean(error) && <Failure message={errorMessage(error)} />}
      <div className={styles.actions}>
        {current && <Button variant="secondary" onClick={onCancel} disabled={busy}>키 변경 취소</Button>}
        <Button type="submit" disabled={busy}>{busy ? '저장 중…' : '키 저장'}</Button>
      </div>
    </form>
  );
}

function SolarKey({ onReady }: { onReady: (ready: boolean) => void }) {
  const { data, error, retry } = useApi(pdfMakerApi.credentials);
  const [saved, setSaved] = useState<Credential | undefined>();
  const [changing, setChanging] = useState(false);
  const current = saved ?? data?.items.find((item) => item.status === 'pending' || item.status === 'active');
  const ready = Boolean(current && !changing);

  useEffect(() => { onReady(ready); }, [ready, onReady]);

  return (
    <Panel title="Solar 연결">
      {error ? <Failure message={error.message} retry={retry} /> : !data
        ? <p role="status">연결 정보를 불러오는 중…</p>
        : ready
          ? <div className={styles.row}>
            <span>{current?.label} · 끝 {current?.maskedSuffix} · {current?.status === 'active' ? '연결됨' : '첫 요청 시 확인'}</span>
            <Button variant="secondary" onClick={() => setChanging(true)}>키 변경</Button>
          </div>
          : <CredentialForm current={current} onCancel={() => setChanging(false)}
            onSaved={(credential) => { setSaved(credential); setChanging(false); }} />}
    </Panel>
  );
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
              const level = student.partResults.find((part) => part.area === source.subject && part.skill === 'reading')?.level;
              const checked = selected.includes(student.id);
              return (
                <label key={student.id} className={styles.target}>
                  <input type="checkbox" checked={checked} disabled={!diagnosed || !level || (!checked && selected.length >= 30)}
                    onChange={() => toggle(student.id)} />
                  <span>{student.displayName} <small>· {languageLabel(student.nativeLanguage)}</small></span>
                  {diagnosed && level ? <span className={styles.reading}>읽기 <LevelPill level={LEVEL_LABELS[level]} /></span>
                    : <small>{diagnosed ? '담당 교과 읽기 결과 없음' : '진단 미완료'}</small>}
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
  return useApi(async () => pdf ? pdf.readPages(from, to) : [], [pdf, from, to]);
}

function PdfComparison({ originals, content, canDownload, title }: {
  originals: PdfPage[];
  content: MaterialContent;
  canDownload: boolean;
  title: string;
}) {
  const [current, setCurrent] = useState(originals[0]?.number ?? 1);
  const { data: preview, error: previewError, retry } = useApi(() => renderConvertedPages(originals, {
    ...content,
    paragraphs: content.paragraphs.map((paragraph) => ({ ...paragraph, page: paragraph.page ?? originals[0].number })),
    hiddenBlockIds: content.hiddenBlockIds ?? [],
  }), [originals, content]);
  const original = originals.find((page) => page.number === current) ?? originals[0];
  const converted = preview?.pages.find((page) => page.number === original.number);
  const { busy, error, run } = useAction();

  function download() {
    if (!preview) return;
    void run(async () => {
      const url = URL.createObjectURL(await exportPreviewPdf(preview, content));
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
        <label>미리보기 쪽 <select value={original.number} onChange={(event) => setCurrent(Number(event.target.value))}>
          {originals.map((page) => <option key={page.number} value={page.number}>{page.number}쪽</option>)}
        </select></label>
        <Button variant="secondary" icon={<Download size={18} />} disabled={!canDownload || !preview?.pages.length || busy} onClick={download}
          title={!canDownload ? '전환 자료를 검토한 뒤 PDF를 저장할 수 있습니다.' : undefined}>
          {busy ? 'PDF 만드는 중…' : '전환본 PDF 내려받기'}
        </Button>
      </div>
      <div className={styles.compare}>
        <figure>
          <figcaption>원본 {original.number}쪽</figcaption>
          <img src={original.imageUrl} alt={`원본 PDF ${original.number}쪽`} />
        </figure>
        <figure>
          <figcaption>전환본 {original.number}쪽</figcaption>
          {converted ? <img src={converted.imageUrl} alt={`전환 PDF ${original.number}쪽`} />
            : preview ? <p>이 쪽의 전환본을 표시할 수 없습니다.</p>
              : previewError ? <Failure message={previewError.message} retry={retry} /> : <p role="status">미리보기를 만드는 중…</p>}
        </figure>
      </div>
      {preview?.notices.length ? <ul className={styles.notices}>{preview.notices.map((notice, index) => <li key={index}>{notice}</li>)}</ul> : null}
      {Boolean(error) && <Failure message={errorMessage(error)} />}
    </Panel>
  );
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
  const original = useSourcePdf(source.fileId);
  const pages = useReviewPages(original.pdf, edition.content);
  const { busy, error, run } = useAction();
  const dirty = edited.paragraphs.some((paragraph, index) => paragraph.text !== edition.content.paragraphs[index].text);
  const editable = edition.status === 'draft' || edition.status === 'reviewed';
  const allTextValid = edited.paragraphs.every((paragraph) => paragraph.text.trim());

  function changeParagraph(id: string, text: string) {
    setEdited((content) => ({ ...content, paragraphs: content.paragraphs.map((p) => p.id === id ? { ...p, text } : p) }));
  }

  function update(action: 'edit' | 'review' | 'publish') {
    void run(async () => {
      const result = action === 'edit'
        ? await pdfMakerApi.edit(edition.id, edited, version)
        : await pdfMakerApi.transition(edition.id, action, version);
      if (!result.etag) throw new Error('다음 편집에 필요한 자료 버전을 받지 못했습니다.');
      setEdition(result.data);
      setVersion(result.etag);
      setEdited(result.data.content);
    });
  }

  return (
    <>
      <p className={styles.status}><strong>{EDITION_LABELS[edition.status]}</strong> · 버전 {edition.revision}</p>
      {!source.fileId ? <p className={styles.muted}>원본 PDF가 없어 본문으로 검토할 수 있습니다.</p>
        : original.error ? <Failure message={original.error} retry={original.retry} />
        : pages.error ? <Failure message={pages.error.message} retry={pages.retry} />
        : pages.data?.length ? <PdfComparison originals={pages.data} content={edition.content} title={edition.title}
          canDownload={!dirty && (edition.status === 'reviewed' || edition.status === 'published')} />
        : <p role="status">PDF 원본을 불러오는 중…</p>}
      <details className={styles.details}>
        <summary><FileText size={18} aria-hidden="true" /> 원본 추출 텍스트</summary>
        <p>{edition.originalContent || source.sourceText || '추출한 텍스트가 없습니다.'}</p>
      </details>
      <Panel title="전환 본문">
        {dirty && <p className={styles.muted}>수정 내용을 저장하면 PDF 미리보기가 갱신됩니다.</p>}
        <div className={styles.form}>
          {edited.paragraphs.map((paragraph, index) => (
            <label key={paragraph.id}>
              {paragraph.page ? `${paragraph.page}쪽 · ` : ''}문단 {index + 1}
              <textarea value={paragraph.text} rows={4} maxLength={10000} readOnly={!editable}
                disabled={busy} onChange={(event) => changeParagraph(paragraph.id, event.target.value)} />
            </label>
          ))}
        </div>
        {Boolean(error) && <Failure message={errorMessage(error)}
          retry={error instanceof ApiError && error.status === 412 ? onReload : undefined} />}
        <div className={styles.actions}>
          {editable && <Button variant="secondary" disabled={busy || !dirty || !allTextValid} onClick={() => update('edit')}>수정 저장</Button>}
          {edition.status === 'draft' && <Button disabled={busy || dirty} onClick={() => update('review')}>검토 완료</Button>}
          {edition.status === 'reviewed' && <Button disabled={busy || dirty} onClick={() => update('publish')}>학생에게 배포</Button>}
        </div>
      </Panel>
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
