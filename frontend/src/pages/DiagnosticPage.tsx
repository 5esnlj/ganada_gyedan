import { Fragment, useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { api, SUBJECT_LABELS } from '../api';
import type {
  AreaCode, DiagnosticNext, DiagnosticResults, DiagnosticStatus, GradeBand, LevelCode,
} from '../api/wire';
import { Button, Chip, LevelPill, ProgressBar } from '../components';
import { useApi } from '../hooks';
import { SUBJECT_TONE, type Level } from '../types';
import styles from './DiagnosticPage.module.css';

const GRADE_LABELS: Record<GradeBand, string> = {
  g34: '초등 3~4학년',
  g56: '초등 5~6학년',
};

const LEVEL_LABELS: Record<LevelCode, Level> = {
  below_beginner: '초급 미달',
  beginner: '초급',
  intermediate: '중급',
  advanced: '고급',
};

const SKILL_LABELS = { listening: '듣기', reading: '읽기' } as const;
const AREAS: AreaCode[] = ['daily', 'korean', 'math', 'social', 'science'];
const scoreFormat = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 2 });
const scoreLabel = (score: number, maxScore: number) => `${scoreFormat.format(score)} / ${scoreFormat.format(maxScore)}점`;

type Question = NonNullable<DiagnosticNext['question']>;
type Assessment =
  | { kind: 'start' }
  | { kind: 'question'; progress: DiagnosticStatus; question: Question }
  | { kind: 'results'; results: DiagnosticResults };

async function loadAssessment(): Promise<Assessment> {
  const current = await api.getDiagnostic();
  if (current.status === 'completed') {
    return { kind: 'results', results: await api.getDiagnosticResults() };
  }
  const seededPlaceholder = current.status === 'in_progress'
    && current.formId === 'form_mock_v1'
    && current.completedQuestions === 0;
  if (current.status === 'not_started' || seededPlaceholder) {
    return { kind: 'start' };
  }
  const { progress, question } = await api.getDiagnosticQuestion();
  if (progress.status === 'completed') {
    return { kind: 'results', results: await api.getDiagnosticResults() };
  }
  if (!question) {
    throw new Error('다음 문항을 찾지 못했습니다. 진행 상황을 다시 불러와 주세요.');
  }
  return { kind: 'question', progress, question };
}

function Page({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return (
    <article className={styles.page}>
      <header className={styles.heading}>
        <h1>{title}</h1>
        {description && <p className={styles.subtle}>{description}</p>}
      </header>
      {children}
    </article>
  );
}

function LoadFeedback({ error, retry }: { error: Error | null; retry: () => void }) {
  return (
    <div className={styles.panel}>
      {error
        ? <><p role="alert">{error.message}</p><div className={styles.actions}><Button onClick={retry}>다시 불러오기</Button></div></>
        : <p role="status">진단평가 정보를 불러오는 중이에요.</p>}
    </div>
  );
}

function useAssessmentAction() {
  const running = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(action: () => Promise<void>) {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    setError(null);
    try {
      // A successful action changes the loaded page, which resets its controls.
      await action();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '요청을 처리하지 못했습니다.');
      running.current = false;
      setBusy(false);
    }
  }

  return { busy, error, run };
}

function GradeSelection({ classId, onStarted }: { classId: string; onStarted: () => void }) {
  const [grade, setGrade] = useState<GradeBand | null>(null);
  const { busy, error, run } = useAssessmentAction();
  const group = useId();

  return (
    <form
      className={styles.panel}
      onSubmit={(event) => {
        event.preventDefault();
        if (!grade) return;
        void run(async () => {
          await api.startDiagnostic(classId, grade);
          onStarted();
        });
      }}
    >
      <fieldset className={`${styles.choices} ${styles.gradeChoices}`} disabled={busy}>
        <legend>응시할 학년을 선택하세요</legend>
        {(Object.entries(GRADE_LABELS) as [GradeBand, string][]).map(([id, label]) => (
          <label key={id} className={styles.choice} data-selected={grade === id}>
            <input type="radio" name={group} value={id} checked={grade === id} onChange={() => setGrade(id)} />
            <span className={styles.choiceContent}><strong>{label}</strong></span>
          </label>
        ))}
      </fieldset>
      <p className={styles.subtle}>제출한 답안은 문항마다 저장됩니다. 나중에 돌아오면 다음 문항부터 이어서 풀 수 있어요.</p>
      {error && <p className={styles.error} role="alert">{error}</p>}
      <div className={styles.actions}>
        <Button type="submit" disabled={!grade || busy}>{busy ? '시작하는 중…' : '진단 시작하기'}</Button>
      </div>
    </form>
  );
}

function MarkedText({ text, spans }: { text: string; spans?: Question['promptSpans'] }) {
  if (!spans?.length) return <>{text}</>;
  return <>{spans.map(({ text: content, style }, index) => {
    if (style === 'strong') return <strong key={index}>{content}</strong>;
    if (style === 'underline') return <u key={index}>{content}</u>;
    return <Fragment key={index}>{content}</Fragment>;
  })}</>;
}

function QuestionAudio({ src }: { src: string }) {
  const [failed, setFailed] = useState(false);
  const [revision, setRevision] = useState(0);

  return (
    <div className={styles.audio}>
      <strong>듣기 음원</strong>
      <audio
        key={revision}
        src={src}
        controls
        preload="metadata"
        aria-label="듣기 음원"
        onError={() => setFailed(true)}
      >
        이 브라우저에서는 음원을 재생할 수 없습니다.
      </audio>
      {failed && (
        <div className={styles.retry}>
          <p role="alert">음원을 불러오지 못했습니다.</p>
          <Button variant="secondary" onClick={() => { setFailed(false); setRevision((value) => value + 1); }}>
            음원 다시 불러오기
          </Button>
        </div>
      )}
    </div>
  );
}

function QuestionContent({ question }: { question: Question }) {
  const { stimulusText, stimulusSpans, stimulusTable, imageUrl, word } = question;
  if (!stimulusText && !stimulusSpans?.length && !stimulusTable && !imageUrl && !word) return null;

  return (
    <div className={styles.stimulus}>
      {word && <p><strong>{word}</strong></p>}
      {(stimulusText || Boolean(stimulusSpans?.length)) && (
        <p className={styles.stimulusText}><MarkedText text={stimulusText} spans={stimulusSpans} /></p>
      )}
      {imageUrl && <img className={styles.questionImage} src={imageUrl} alt="문항의 그림 자료" />}
      {stimulusTable && (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>{stimulusTable.headers.map((header, index) => <th key={index} scope="col">{header}</th>)}</tr>
            </thead>
            <tbody>
              {stimulusTable.rows.map((row, index) => (
                <tr key={index}>{row.map((cell, column) => <td key={column}>{cell}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function QuestionPanel({ progress, question, onSaved }: {
  progress: DiagnosticStatus;
  question: Question;
  onSaved: () => void;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const { busy, error, run } = useAssessmentAction();
  const prompt = useRef<HTMLHeadingElement>(null);
  const group = useId();
  const hasImageChoice = question.options.some((option) => option.imageUrl);
  const sectionProgress = question.sectionNumber != null && question.sectionTotal != null
    ? `문항 ${question.sectionNumber} / ${question.sectionTotal}`
    : `문항 ${question.sequence}`;

  useEffect(() => { prompt.current?.focus(); }, []);

  function submit(optionId: string | null) {
    void run(async () => {
      await api.submitDiagnosticAnswer(question.id, optionId);
      onSaved();
    });
  }

  return (
    <>
      <div className={styles.progress}>
        <div className={styles.progressLabel}>
          <span>진단 진행률</span>
          <strong>{progress.completedQuestions} / {progress.totalQuestions}문항 완료</strong>
        </div>
        <ProgressBar
          value={progress.totalQuestions ? progress.completedQuestions / progress.totalQuestions : 0}
          label="진단 진행률"
        />
      </div>
      <form
        className={styles.panel}
        onSubmit={(event) => { event.preventDefault(); if (selected !== null) submit(selected); }}
      >
        <div className={styles.sectionLabel}>
          <span>{SUBJECT_LABELS[question.area]} · {SKILL_LABELS[question.skill]}</span>
          <span>{sectionProgress}</span>
        </div>
        <h2 ref={prompt} tabIndex={-1} className={styles.prompt}>
          <MarkedText text={question.prompt} spans={question.promptSpans} />
        </h2>
        {question.audioUrl && <QuestionAudio src={question.audioUrl} />}
        <QuestionContent question={question} />
        <fieldset className={`${styles.choices} ${hasImageChoice ? styles.imageChoices : ''}`} disabled={busy}>
          <legend>답을 골라 주세요</legend>
          {question.options.map((option, index) => (
            <label key={option.id} className={styles.choice} data-selected={selected === option.id}>
              <input
                type="radio"
                name={group}
                value={option.id}
                checked={selected === option.id}
                onChange={() => setSelected(option.id)}
              />
              <span className={styles.choiceContent}>
                {option.text || `보기 ${index + 1}`}
                {option.imageUrl && <img src={option.imageUrl} alt="" />}
              </span>
            </label>
          ))}
        </fieldset>
        {error && (
          <div className={styles.retry}>
            <p className={styles.error} role="alert">{error}</p>
            <Button variant="secondary" onClick={onSaved}>진행 상황 다시 불러오기</Button>
          </div>
        )}
        <div className={styles.actions}>
          <Button variant="secondary" disabled={busy} onClick={() => submit(null)}>모르겠어요</Button>
          <Button type="submit" disabled={busy || selected === null}>{busy ? '저장하는 중…' : '답안 제출'}</Button>
        </div>
      </form>
    </>
  );
}

function ResultCard({ title, result }: {
  title: string;
  result?: { score: number; maxScore: number; level: LevelCode };
}) {
  return (
    <div className={styles.result}>
      <h3>{title}</h3>
      {result ? (
        <>
          <p className={styles.score}>{scoreLabel(result.score, result.maxScore)}</p>
          <LevelPill level={LEVEL_LABELS[result.level]} />
        </>
      ) : <p className={styles.subtle}>결과 없음</p>}
    </div>
  );
}

function AssessmentSummary({ summary }: { summary: NonNullable<DiagnosticResults['assessmentSummary']> }) {
  return (
    <section className={styles.panel}>
      <h2>검사 결과 요약</h2>
      <p className={styles.subtle}>{GRADE_LABELS[summary.gradeBand]} 검사</p>
      <div className={styles.results}>
        <ResultCard title="듣기" result={summary.listening} />
        <ResultCard title="읽기" result={summary.reading} />
        <ResultCard title="듣기·읽기 종합" result={summary.overall} />
      </div>
      <div className={styles.communication}>
        <h3>생활한국어</h3>
        <span>{scoreLabel(summary.communication.score, summary.communication.maxScore)}</span>
        <strong>{summary.communication.status === 'adequate' ? '양호' : '보완 필요'}</strong>
      </div>
      {summary.subjects.length > 0 && (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead><tr><th scope="col">교과</th><th scope="col">점수</th><th scope="col">수준</th></tr></thead>
            <tbody>
              {summary.subjects.map((subject) => (
                <tr key={subject.area}>
                  <th scope="row">{SUBJECT_LABELS[subject.area]}</th>
                  <td>{scoreLabel(subject.score, subject.maxScore)}</td>
                  <td><LevelPill level={LEVEL_LABELS[subject.level]} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Results({ results, onContinue }: { results: DiagnosticResults; onContinue?: () => Promise<void> }) {
  const [area, setArea] = useState<AreaCode>('daily');
  const { busy, error, run } = useAssessmentAction();
  const listening = results.parts.find((part) => part.area === area && part.skill === 'listening');
  const reading = results.parts.find((part) => part.area === area && part.skill === 'reading');
  const overall = results.areas.find((result) => result.area === area);

  return (
    <Page title="진단평가 결과" description={results.isSynthetic ? '연습용 진단 결과입니다.' : undefined}>
      {results.assessmentSummary && <AssessmentSummary summary={results.assessmentSummary} />}
      <section className={styles.panel}>
        <h2>영역별 결과</h2>
        <div className={styles.areaPicker} role="group" aria-label="진단 영역 선택">
          {AREAS.map((value) => (
            <Chip key={value} selected={area === value} onClick={() => setArea(value)}>{SUBJECT_LABELS[value]}</Chip>
          ))}
        </div>
        <div className={styles.results} data-tone={SUBJECT_TONE[SUBJECT_LABELS[area]]}>
          <ResultCard title="듣기" result={listening} />
          <ResultCard title="읽기" result={reading} />
          <ResultCard title="영역 종합" result={overall} />
        </div>
      </section>
      {onContinue && (
        <div className={styles.actions}>
          {error && <p className={styles.error} role="alert">{error}</p>}
          <Button disabled={busy} onClick={() => void run(onContinue)}>
            {busy ? '클래스를 여는 중…' : '클래스로 가기'}
          </Button>
        </div>
      )}
    </Page>
  );
}

export function DiagnosticPage({ classId, onContinue }: { classId: string; onContinue: () => Promise<void> }) {
  const { data, error, retry } = useApi(loadAssessment, [classId]);
  if (data?.kind === 'results') return <Results results={data.results} onContinue={onContinue} />;
  return (
    <Page title="한국어 진단평가">
      {!data ? <LoadFeedback error={error} retry={retry} /> : data.kind === 'start'
        ? <GradeSelection classId={classId} onStarted={retry} />
        : <QuestionPanel key={data.question.id} progress={data.progress} question={data.question} onSaved={retry} />}
    </Page>
  );
}

export function DiagnosticResultPage() {
  const { data, error, retry } = useApi(api.getDiagnosticResults);
  return data
    ? <Results results={data} />
    : <Page title="진단평가 결과"><LoadFeedback error={error} retry={retry} /></Page>;
}
