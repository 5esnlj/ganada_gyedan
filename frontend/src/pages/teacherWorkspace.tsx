import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { pdfMakerApi, type Credential } from '../api/pdfMaker';
import type { LevelCode } from '../api/wire';
import { Button } from '../components';
import { useApi } from '../hooks';
import type { Level } from '../types';
import styles from './PdfMakerPage.module.css';

const languageNames = new Intl.DisplayNames(['ko'], { type: 'language' });

/** 교사 작업 화면(자료 제작·문항 생성)이 함께 쓰는 패널, 오류 표시, 요청 상태, Solar 키 연결. */
export const LEVEL_LABELS: Record<LevelCode, Level> = {
  below_beginner: '초급 미달', beginner: '초급', intermediate: '중급', advanced: '고급',
};
export const errorMessage = (error: unknown) => error instanceof Error ? error.message : '요청을 처리하지 못했습니다.';

export function languageLabel(code: string) {
  if (!code) return '모국어 미등록';
  try { return languageNames.of(code) ?? code; } catch { return code; }
}

export function Failure({ message, retry }: { message: string; retry?: () => void }) {
  return (
    <div className={styles.failure} role="alert">
      <p>{message}</p>
      {retry && <Button variant="secondary" onClick={retry}>다시 불러오기</Button>}
    </div>
  );
}

export function Panel({ title, children }: { title: string; children: ReactNode }) {
  return <section className={styles.panel}><h2>{title}</h2>{children}</section>;
}

export function useAction() {
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

export function SolarKey({ onReady }: { onReady: (ready: boolean) => void }) {
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
