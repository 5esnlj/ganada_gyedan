import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ChevronLeft, ChevronRight, Sparkles } from 'lucide-react';
import { ApiError } from '../api';
import { pdfMakerApi, type StudentTarget } from '../api/pdfMaker';
import { quizApi, type QuestionEdit, type QuizJob, type TeacherQuestion, type TeacherQuiz } from '../api/quizzes';
import type { AreaCode } from '../api/wire';
import { Button, Chip, LevelPill, PageHeader, TableCard } from '../components';
import table from '../components/TableCard/TableCard.module.css';
import { useApi } from '../hooks';
import {
  CATEGORY_LABELS, categoryResultText, firstAttemptSummary, groupByLevel, planText, QUIZ_STATUS_LABELS,
} from '../quizzes/summary';
import styles from './PdfMakerPage.module.css';
import quiz from './QuizMakerPage.module.css';
import { errorMessage, Failure, languageLabel, LEVEL_LABELS, Panel, SolarKey, useAction } from './teacherWorkspace';

const JOB_LABELS: Record<QuizJob['status'], string> = {
  queued: '대기 중', running: '만드는 중', succeeded: '완료', failed: '실패', unknown: '상태 확인 필요',
};

function useQuizJobs(classId: string) {
  const [jobs, setJobs] = useState<QuizJob[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let mounted = true;
    let timer: number | undefined;
    async function load() {
      try {
        const result = await quizApi.jobs(classId);
        if (!mounted) return;
        setJobs(result.items);
        setError(null);
        if (result.items.some((job) => job.status === 'queued' || job.status === 'running')) timer = window.setTimeout(load, 1500);
      } catch (cause) {
        if (mounted) setError(errorMessage(cause));
      }
    }
    void load();
    return () => { mounted = false; if (timer !== undefined) window.clearTimeout(timer); };
  }, [classId, revision]);
  return { jobs, error, retry: () => setRevision((value) => value + 1) };
}

/** 교사 07 · 새 문항 만들기: 원본 자료와 학생을 고르면 학생마다 진단 결과에 맞춘 확인 문제를 만든다. */
function NewQuiz({ classId, subject, students, onQueued }: {
  classId: string; subject: AreaCode; students: StudentTarget[]; onQueued: () => void;
}) {
  const sources = useApi(() => pdfMakerApi.sources(classId, subject), [classId, subject]);
  const [sourceId, setSourceId] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const [keyReady, setKeyReady] = useState(false);
  const { busy, error, run } = useAction();
  const prior = useRef<{ body: string; key: string }>();
  const items = sources.data?.items ?? [];

  function toggle(id: string) {
    setSelected((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!keyReady || !sourceId || !selected.length) return;
    void run(async () => {
      const input = { sourceId, studentMemberIds: [...selected].sort() };
      const body = JSON.stringify(input);
      if (prior.current?.body !== body) prior.current = { body, key: crypto.randomUUID() };
      await quizApi.startJobs(classId, input, prior.current.key);
      prior.current = undefined;
      setSelected([]);
      onQueued();
    });
  }

  return (
    <>
      <SolarKey onReady={setKeyReady} />
      <Panel title="새 문항 만들기">
        {sources.error ? <Failure message={sources.error.message} retry={sources.retry} /> : (
          <form className={styles.form} onSubmit={submit}>
            <label>자료<select aria-label="자료" value={sourceId} onChange={(event) => setSourceId(event.target.value)} required disabled={busy}>
              <option value="">자료를 고르세요</option>
              {items.map((item) => <option key={item.id} value={item.id}>{item.title}{item.unit ? ` · ${item.unit}` : ''}</option>)}
            </select></label>
            <p className={styles.muted}>선택한 학생에게 배포된 전환본으로 문항을 만듭니다.</p>
            <fieldset className={styles.targets} disabled={busy}>
              <legend>받을 학생 · {selected.length}명</legend>
              {students.map((student) => {
                const diagnosed = student.diagnosticStatus === 'completed';
                const level = student.partResults.find((part) => part.area === subject && part.skill === 'reading')?.level;
                const checked = selected.includes(student.id);
                return (
                  <label key={student.id} className={styles.target}>
                    <input type="checkbox" checked={checked} disabled={!diagnosed || (!checked && selected.length >= 30)}
                      onChange={() => toggle(student.id)} />
                    <span>{student.displayName} <small>· {languageLabel(student.nativeLanguage)}</small></span>
                    {diagnosed && level ? <span className={styles.reading}>읽기 <LevelPill level={LEVEL_LABELS[level]} /></span>
                      : <small>{diagnosed ? '읽기 결과 없음' : '진단 미완료'}</small>}
                  </label>
                );
              })}
            </fieldset>
            {Boolean(error) && <Failure message={errorMessage(error)} />}
            <div className={styles.actions}>
              <Button type="submit" icon={<Sparkles size={18} />} disabled={!keyReady || busy || !sourceId || !selected.length}>
                {busy ? '요청 중…' : '문항 만들기'}
              </Button>
            </div>
          </form>
        )}
      </Panel>
    </>
  );
}

function QuestionEditor({ question, sentences, onCancel, onSave, busy }: {
  question: TeacherQuestion; busy: boolean; onCancel: () => void; onSave: (body: QuestionEdit) => void;
  sentences: Map<string, string>;
}) {
  const [prompt, setPrompt] = useState(question.prompt);
  const [choices, setChoices] = useState(question.choices);
  const [correct, setCorrect] = useState(question.correctOptionId);
  const [explanation, setExplanation] = useState(question.explanation);
  const [evidence, setEvidence] = useState(question.sourceSentenceIds.filter((id) => sentences.has(id)));
  const valid = prompt.trim() && explanation.trim() && choices.every((c) => c.text.trim()) && evidence.length > 0;
  return (
    <div className={styles.form}>
      <label>문제<textarea rows={2} value={prompt} onChange={(e) => setPrompt(e.target.value)} maxLength={10000} /></label>
      {choices.map((choice, index) => (
        <label key={choice.id} className={quiz.editChoice}>
          <input type="radio" name="correct" checked={correct === choice.id} onChange={() => setCorrect(choice.id)} aria-label={`${index + 1}번 정답`} />
          <span>{index + 1}</span>
          <input value={choice.text} maxLength={1000} aria-label={`${index + 1}번 보기`}
            onChange={(e) => setChoices((list) => list.map((c) => c.id === choice.id ? { ...c, text: e.target.value } : c))} />
        </label>
      ))}
      <label>해설<textarea rows={2} value={explanation} onChange={(e) => setExplanation(e.target.value)} maxLength={10000} /></label>
      <fieldset className={styles.targets} disabled={busy}>
        <legend>정답의 근거 문장</legend>
        {[...sentences].map(([id, text]) => (
          <label key={id} className={styles.target}>
            <input type="checkbox" checked={evidence.includes(id)}
              onChange={() => setEvidence((rows) => rows.includes(id) ? rows.filter((row) => row !== id) : [...rows, id])} />
            <span>{text}</span>
          </label>
        ))}
      </fieldset>
      <div className={styles.actions}>
        <Button variant="secondary" onClick={onCancel} disabled={busy}>취소</Button>
        <Button disabled={busy || !valid} onClick={() => onSave({
          prompt: prompt.trim(), choices: choices.map((c) => ({ ...c, text: c.text.trim() })), correctOptionId: correct,
          explanation: explanation.trim(), sourceSentenceIds: evidence, maxPoints: question.maxPoints,
        })}>저장</Button>
      </div>
    </div>
  );
}

/** 교사 08 · 문항 검토: 문항과 보기, 정답(연한 초록), 근거 문장을 한 문항씩 보고 고친 뒤 배포한다. */
function QuizReview({ quizId, studentName, onChanged }: { quizId: string; studentName: string; onChanged: () => void }) {
  const loaded = useApi(() => quizApi.teacherQuiz(quizId), [quizId]);
  const [current, setCurrent] = useState<{ data: TeacherQuiz; etag: string } | null>(null);
  const [index, setIndex] = useState(0);
  const [editing, setEditing] = useState(false);
  const { busy, error, run } = useAction();

  useEffect(() => {
    if (loaded.data?.etag) setCurrent({ data: loaded.data.data, etag: loaded.data.etag });
  }, [loaded.data]);

  if (loaded.error) return <Failure message={loaded.error.message} retry={loaded.retry} />;
  if (!current) return <p role="status">문항을 불러오는 중…</p>;
  const { data } = current;
  const question = data.questions[Math.min(index, data.questions.length - 1)];
  const sentences = new Map((data.passage?.paragraphs ?? []).map((p) => [p.id, p.text]));
  const editable = data.status === 'draft' || data.status === 'reviewed';
  const results = firstAttemptSummary(data);

  function apply(action: () => Promise<{ data: TeacherQuiz; etag: string | null }>) {
    void run(async () => {
      const result = await action();
      if (!result.etag) throw new Error('다음 편집에 필요한 버전을 받지 못했습니다.');
      setCurrent({ data: result.data, etag: result.etag });
      setEditing(false);
      onChanged();
    });
  }

  return (
    <Panel title={`${studentName} · ${QUIZ_STATUS_LABELS[data.status]}`}>
      {data.generation && (
        <p className={quiz.plan}>
          <LevelPill level={LEVEL_LABELS[data.generation.baseLevel]} />
          <span>{planText(data.generation.plan)}</span>
          {data.generation.adjustment && <em>{data.generation.adjustment}</em>}
        </p>
      )}
      <div className={quiz.pager}>
        <Button variant="secondary" aria-label="이전 문항" icon={<ChevronLeft size={18} />} disabled={index === 0 || editing} onClick={() => setIndex(index - 1)} />
        <b>{index + 1} / {data.questions.length}</b>
        <Button variant="secondary" aria-label="다음 문항" icon={<ChevronRight size={18} />} disabled={index >= data.questions.length - 1 || editing} onClick={() => setIndex(index + 1)} />
        <span className={quiz.tags}>
          {question.category && <Chip>{CATEGORY_LABELS[question.category]}</Chip>}
          {question.isChallenge && <Chip selected>도전</Chip>}
        </span>
      </div>
      {editing ? <QuestionEditor key={question.id} question={question} sentences={sentences} busy={busy} onCancel={() => setEditing(false)}
        onSave={(body) => apply(() => quizApi.editQuestion(data.id, question.id, body, current.etag))} />
        : <>
          <p className={quiz.prompt}>{question.prompt}</p>
          <ol className={quiz.choices}>
            {question.choices.map((choice) => (
              <li key={choice.id} className={choice.id === question.correctOptionId ? quiz.correct : undefined}>{choice.text}</li>
            ))}
          </ol>
          <p className={styles.muted}>해설 · {question.explanation}</p>
          <p className={quiz.source}>근거 · {question.sourceSentenceIds.map((id) => sentences.get(id) ?? id).join(' ')}</p>
        </>}
      {data.status === 'published' && (
        <p className={quiz.result}>
          {results.answered ? <>첫 풀이 {categoryResultText(results)}{results.challenge.total ? ` · 도전 ${results.challenge.correct}/${results.challenge.total}` : ''}</> : '아직 풀지 않았어요'}
          {data.generation?.nextAdjustment && <em>{data.generation.nextAdjustment}</em>}
        </p>
      )}
      {Boolean(error) && <Failure message={errorMessage(error)} retry={error instanceof ApiError && error.status === 412 ? loaded.retry : undefined} />}
      {!editing && (
        <div className={styles.actions}>
          {editable && <Button variant="secondary" disabled={busy} onClick={() => setEditing(true)}>수정</Button>}
          {data.status === 'draft' && <Button disabled={busy} onClick={() => apply(() => quizApi.transition(data.id, 'review', current.etag))}>검토 완료</Button>}
          {data.status === 'reviewed' && <Button disabled={busy} onClick={() => apply(() => quizApi.transition(data.id, 'publish', current.etag))}>학생에게 배포</Button>}
        </div>
      )}
    </Panel>
  );
}

export function QuizMakerPage({ classId, subject }: { classId: string; subject: AreaCode }) {
  const roster = useApi(() => pdfMakerApi.students(classId), [classId]);
  const quizzes = useApi(() => quizApi.teacherQuizzes(classId, subject), [classId, subject]);
  const jobs = useQuizJobs(classId);
  const [creating, setCreating] = useState(false);
  const [levelTab, setLevelTab] = useState<string | null>(null);
  const [quizId, setQuizId] = useState<string | null>(null);
  const studentName = (id: string) => roster.data?.items.find((row) => row.id === id)?.displayName ?? id;
  const pending = jobs.jobs?.filter((job) => job.status !== 'succeeded') ?? [];
  const finished = jobs.jobs?.filter((job) => job.status === 'succeeded').length ?? 0;
  const refresh = quizzes.retry;

  // 작업이 끝나면 문항 목록을 다시 읽는다.
  const seen = useRef(0);
  useEffect(() => { if (finished !== seen.current) { seen.current = finished; refresh(); } }, [finished]); // eslint-disable-line react-hooks/exhaustive-deps

  const groups = groupByLevel(quizzes.data?.items ?? []);
  const activeGroup = groups.find((group) => group.key === levelTab) ?? groups[0];
  const selected = quizId ?? activeGroup?.quizzes[0]?.id ?? null;

  return (
    <div className={styles.page}>
      <PageHeader title="문항 생성" actions={<Button onClick={() => setCreating(!creating)}>{creating ? '목록으로' : '새 문항 만들기'}</Button>} />
      {creating && (roster.error ? <Failure message={roster.error.message} retry={roster.retry} />
        : roster.data ? <NewQuiz classId={classId} subject={subject} students={roster.data.items} onQueued={() => { jobs.retry(); setCreating(false); }} />
        : <p role="status">학생 목록을 불러오는 중…</p>)}
      {jobs.error ? <Failure message={jobs.error} retry={jobs.retry} /> : pending.length > 0 && (
        <Panel title="문항 생성 작업">
          <ul className={styles.records}>
            {pending.map((job) => (
              <li key={job.id}><strong>{studentName(job.studentMemberId)} · {JOB_LABELS[job.status]}</strong>{job.errorMessage && <p>{job.errorMessage}</p>}</li>
            ))}
          </ul>
        </Panel>
      )}
      {quizzes.error ? <Failure message={quizzes.error.message} retry={quizzes.retry} /> : !quizzes.data
        ? <p role="status">문항 목록을 불러오는 중…</p>
        : !groups.length ? <p className={styles.muted}>아직 만든 문항이 없습니다.</p>
        : <>
          <div className={quiz.tabs}>
            {groups.map((group) => (
              <Chip key={group.key} selected={group.key === activeGroup?.key} onClick={() => { setLevelTab(group.key); setQuizId(null); }}>
                {group.key === 'other' ? '기타' : LEVEL_LABELS[group.key]} {group.quizzes.length}
              </Chip>
            ))}
          </div>
          <div className={quiz.layout}>
            <TableCard title="문항 묶음">
              <table className={table.table}><thead><tr><th>학생</th><th>자료</th><th>상태</th></tr></thead><tbody>
                {activeGroup?.quizzes.map((item) => (
                  <tr key={item.id} className={item.id === selected ? quiz.selectedRow : undefined} onClick={() => setQuizId(item.id)}>
                    <td><button type="button" className={quiz.rowButton} onClick={() => setQuizId(item.id)}>{studentName(item.studentMemberId)}</button></td>
                    <td>{item.title}</td>
                    <td>{QUIZ_STATUS_LABELS[item.status]}</td>
                  </tr>
                ))}
              </tbody></table>
            </TableCard>
            {selected && (() => {
              const item = quizzes.data.items.find((q) => q.id === selected);
              return item ? <QuizReview key={selected} quizId={selected} studentName={studentName(item.studentMemberId)} onChanged={refresh} /> : null;
            })()}
          </div>
        </>}
    </div>
  );
}
