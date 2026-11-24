import { useRef, useState } from 'react';
import { BookOpen } from 'lucide-react';
import { SUBJECT_LABELS } from '../api';
import { quizApi, type Explanation, type StudentQuiz } from '../api/quizzes';
import { Button, Chip, PageHeader, ProgressBar, TableCard } from '../components';
import table from '../components/TableCard/TableCard.module.css';
import { useApi } from '../hooks';
import { nextUnanswered, progressLabel } from '../quizzes/summary';
import styles from './StudentQuizPage.module.css';

const message = (error: unknown) => error instanceof Error ? error.message : '요청을 처리하지 못했습니다.';

/** 학생 07 · 문제 풀기: 한 문제씩 고르고 제출한다. 다시 풀기는 틀린 문제 하나만 연다. */
function Solve({ quiz, index, onAnswered, onExit }: {
  quiz: StudentQuiz; index: number; onAnswered: () => void; onExit: () => void;
}) {
  const question = quiz.questions[index];
  const [choice, setChoice] = useState<string | null>(null);
  const [showPassage, setShowPassage] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const done = quiz.progress.submittedQuestionCount;
  // 같은 제출을 다시 보내도 한 번만 기록되도록 문항 화면마다 요청 키를 둔다.
  const requestKey = useRef(crypto.randomUUID());

  async function submit() {
    if (!choice || busy) return;
    setBusy(true); setError(null);
    try {
      await quizApi.answer(quiz.id, question.id, choice, requestKey.current);
      requestKey.current = crypto.randomUUID();
      setChoice(null);
      onAnswered();
    } catch (cause) {
      setError(message(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className={styles.card}>
      <div className={styles.head}>
        <b>{index + 1} / {quiz.questions.length}</b>
        <ProgressBar value={quiz.questions.length ? done / quiz.questions.length : 0} label="푼 문제" />
        <Button variant="secondary" icon={<BookOpen size={18} />} onClick={() => setShowPassage(!showPassage)}>자료 보기</Button>
      </div>
      {showPassage && <div className={styles.passage}>{quiz.passage.paragraphs.map((p) => <p key={p.id}>{p.text}</p>)}</div>}
      <p className={styles.prompt}>{question.isChallenge && <Chip selected>도전</Chip>} {question.prompt}</p>
      <div className={styles.choices} role="radiogroup" aria-label="보기">
        {question.choices.map((option, i) => (
          <button key={option.id} type="button" role="radio" aria-checked={choice === option.id}
            className={choice === option.id ? styles.picked : undefined} onClick={() => { if (choice !== option.id) requestKey.current = crypto.randomUUID(); setChoice(option.id); }} disabled={busy}>
            <span>{i + 1}</span>{option.text}
          </button>
        ))}
      </div>
      {error && <p role="alert" className={styles.alert}>{error}</p>}
      <div className={styles.actions}>
        <Button variant="secondary" onClick={onExit} disabled={busy}>나가기</Button>
        <Button onClick={submit} disabled={!choice || busy}>{busy ? '제출 중…' : '제출'}</Button>
      </div>
    </section>
  );
}

/** 학생 08 · 결과: 처음 푼 점수, 틀린 문제와 다시 푼 횟수, 해설과 근거 문장 */
function Result({ quiz, onRetry }: { quiz: StudentQuiz; onRetry: (index: number) => void }) {
  const [opened, setOpened] = useState<Record<string, Explanation | string>>({});
  const sentences = new Map(quiz.passage.paragraphs.map((p) => [p.id, p.text]));
  const { progress } = quiz;

  async function explain(questionId: string) {
    try { const value = await quizApi.explanation(quiz.id, questionId); setOpened((o) => ({ ...o, [questionId]: value })); }
    catch (cause) { setOpened((o) => ({ ...o, [questionId]: message(cause) })); }
  }

  return (
    <section className={styles.card}>
      <p className={styles.score}><b>{progress.firstCorrectCount ?? 0} / {progress.questionCount}</b> 맞았어요</p>
      {progress.wrongQuestions.length === 0 ? <p className={styles.muted}>틀린 문제가 없어요.</p> : (
        <ul className={styles.wrong}>
          {progress.wrongQuestions.map((wrong) => {
            const question = quiz.questions.find((q) => q.id === wrong.questionId)!;
            const detail = opened[wrong.questionId];
            return (
              <li key={wrong.questionId}>
                <div className={styles.wrongHead}>
                  <b>{wrong.position}번</b>
                  <span className={styles.muted}>다시 푼 횟수 {wrong.retryCount}</span>
                  <Button variant="secondary" onClick={() => explain(wrong.questionId)}>해설 볼래요</Button>
                  <Button onClick={() => onRetry(quiz.questions.indexOf(question))}>다시 풀어볼래요</Button>
                </div>
                {typeof detail === 'string' && <p role="alert" className={styles.alert}>{detail}</p>}
                {detail && typeof detail !== 'string' && (
                  <div className={styles.explain}>
                    <p>{detail.explanation}</p>
                    {detail.sourceSentenceIds.length > 0 && (
                      <p className={styles.highlight}>자료에서 · {detail.sourceSentenceIds.map((id) => sentences.get(id)).filter(Boolean).join(' ')}</p>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function QuizView({ quizId, onBack }: { quizId: string; onBack: () => void }) {
  const { data, error, retry } = useApi(() => quizApi.studentQuiz(quizId), [quizId]);
  const [retrying, setRetrying] = useState<number | null>(null);
  if (error) return <div role="alert"><p>{error.message}</p><Button onClick={retry}>다시 불러오기</Button></div>;
  if (!data) return <p role="status">문제를 불러오는 중…</p>;
  // 처음 풀이가 끝나지 않았으면 아직 답하지 않은 문제부터 이어서 푼다.
  const next = nextUnanswered(data.questions.map((q) => q.id), new Set(data.progress.answeredQuestionIds));
  return (
    <>
      <PageHeader title={data.title} sub={SUBJECT_LABELS[data.subject]} actions={<Button variant="secondary" onClick={onBack}>문항 목록</Button>} />
      {retrying !== null ? <Solve key={`retry-${retrying}`} quiz={data} index={retrying} onExit={() => setRetrying(null)} onAnswered={() => { setRetrying(null); retry(); }} />
        : next !== null ? <Solve key={next} quiz={data} index={next} onExit={onBack} onAnswered={retry} />
        : <Result quiz={data} onRetry={setRetrying} />}
    </>
  );
}

/** 학생 06 · 문항 목록 */
export function StudentQuizPage({ classId }: { classId: string }) {
  const { data, error, retry } = useApi(() => quizApi.studentQuizzes(classId), [classId]);
  const [quizId, setQuizId] = useState<string | null>(null);
  if (quizId) return <QuizView key={quizId} quizId={quizId} onBack={() => { setQuizId(null); retry(); }} />;
  return (
    <>
      <PageHeader title="내 문항" />
      {error ? <div role="alert"><p>{error.message}</p><Button onClick={retry}>다시 불러오기</Button></div>
        : !data ? <p role="status">불러오는 중…</p>
        : (
          <TableCard title="문항 묶음" grow>
            <table className={table.table}><thead><tr><th>문항</th><th>교과</th><th>진행</th><th>상태</th><th /></tr></thead><tbody>
              {data.items.map((item) => (
                <tr key={item.id}>
                  <td><b>{item.title}</b> <small className={styles.muted}>{item.questionCount}문항</small></td>
                  <td>{SUBJECT_LABELS[item.subject]}</td>
                  <td><ProgressBar value={item.questionCount ? item.progress.submittedQuestionCount / item.questionCount : 0} width={120} label="푼 문제" /></td>
                  <td className={item.progress.status === 'not_started' || item.progress.currentWrongCount ? styles.alert : undefined}>{progressLabel(item.progress)}</td>
                  <td><Button variant="secondary" onClick={() => setQuizId(item.id)}>{item.progress.status === 'completed' ? '결과 보기' : '풀기'}</Button></td>
                </tr>
              ))}
              {!data.items.length && <tr><td colSpan={5}>아직 받은 문항이 없어요.</td></tr>}
            </tbody></table>
          </TableCard>
        )}
    </>
  );
}
