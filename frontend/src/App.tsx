import { useEffect, useState, type FormEvent } from 'react';
import { api, ApiError, SUBJECT_LABELS } from './api';
import type { AreaCode, ClassOut, SessionOut } from './api/wire';
import { AppShell, Button, Sidebar, TopBar, type NavKey } from './components';
import { useApi } from './hooks';
import { ClassSelectionPage } from './pages/ClassSelectionPage';
import { DiagnosticPage, DiagnosticResultPage } from './pages/DiagnosticPage';
import { PdfMakerPage } from './pages/PdfMakerPage';
import { QuizMakerPage } from './pages/QuizMakerPage';
import { StudentQuizPage } from './pages/StudentQuizPage';
import { StudentDashboard } from './pages/StudentDashboard';
import { StudentMaterialsPage, StudentMaterialView } from './pages/StudentMaterialsPage';
import { TeacherDashboard } from './pages/TeacherDashboard';
import type { Role } from './types';
import styles from './App.module.css';

const DEMO_LOGIN_IDS: Record<Role, string> = { teacher: 't_korean', student: 's_a' };

export default function App() {
  const [session, setSession] = useState<SessionOut | null>(null);
  const [booting, setBooting] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    api.me().then((s) => { if (alive) setSession(s); }).catch((e: unknown) => { if (alive && !(e instanceof ApiError && e.status === 401)) setError(e instanceof Error ? e.message : '로그인 상태를 확인하지 못했습니다.'); }).finally(() => { if (alive) setBooting(false); });
    return () => { alive = false; };
  }, []);
  async function logout() {
    try { await api.logout(); setSession(null); setError(null); }
    catch (e) { if (e instanceof ApiError && e.status === 401) { setSession(null); setError(null); } else setError(e instanceof Error ? e.message : '로그아웃에 실패했습니다.'); }
  }
  if (booting) return <main className={styles.login}><p>로그인 상태 확인 중…</p></main>;
  if (!session) return <Login initialError={error} onLogin={(s) => { setSession(s); setError(null); }} />;
  return <Authenticated key={session.user.id} session={session} onLogout={logout} sessionError={error}
    refreshSession={async () => {
      const refreshed = await api.me();
      setSession((current) => current?.user.id === refreshed.user.id ? refreshed : current);
    }} />;
}

function Login({ initialError, onLogin }: { initialError: string | null; onLogin: (session: SessionOut) => void }) {
  const [role, setRole] = useState<Role>('teacher');
  const [loginId, setLoginId] = useState(DEMO_LOGIN_IDS.teacher);
  const [password, setPassword] = useState('mock-pass-123');
  const [error, setError] = useState(initialError);
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError(null);
    try { onLogin(await api.login(loginId, password, role)); }
    catch (e) { setError(e instanceof Error ? e.message : '로그인에 실패했습니다.'); }
    finally { setBusy(false); }
  }
  return <main className={styles.login}><h1>가나다계단</h1><h2>API 연동 확인용 로그인</h2>
    <p>개발용 가상 계정으로 접속합니다.</p>
    <form onSubmit={submit} className={styles.loginForm}>
      <label>역할<select value={role} onChange={(e) => { const value = e.target.value as Role; setRole(value); setLoginId(DEMO_LOGIN_IDS[value]); }}><option value="teacher">교사</option><option value="student">학생</option></select></label>
      <label>아이디<input value={loginId} onChange={(e) => setLoginId(e.target.value)} autoComplete="username" required /></label>
      <label>비밀번호<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required /></label>
      {error && <p role="alert">{error}</p>}<Button type="submit" disabled={busy}>{busy ? '확인 중…' : '로그인'}</Button>
    </form>
    <p>국어 교사 <code>{DEMO_LOGIN_IDS.teacher}</code> · 학생 <code>{DEMO_LOGIN_IDS.student}</code><br />예시 비밀번호 <code>mock-pass-123</code></p>
    <small>진단 미완료 학생: s_pending · 클래스 미가입 학생: s_new</small>
  </main>;
}

function Authenticated({ session, onLogout, sessionError, refreshSession }: {
  session: SessionOut;
  onLogout: () => void;
  sessionError: string | null;
  refreshSession: () => Promise<void>;
}) {
  const { data, error, retry } = useApi(api.getClasses, [session.user.id]);
  const [selectedClassId, setSelectedClassId] = useState<string | null>(null);
  const selected = data?.items.find((classroom) => classroom.id === selectedClassId);
  const openClassSelection = () => setSelectedClassId(null);
  const logoutButton = <Button variant="secondary" onClick={onLogout}>로그아웃</Button>;
  if (selected && session.nextAction === 'diagnostic') {
    return <AppShell topBar={<TopBar userName={session.user.displayName} compact actions={logoutButton} />}>
      <div><Button variant="secondary" onClick={openClassSelection}>클래스 선택</Button></div>
      {sessionError && <p role="alert">{sessionError}</p>}
      <DiagnosticPage key={selected.id} classId={selected.id} onContinue={refreshSession} />
    </AppShell>;
  }
  if (selected && session.nextAction === 'classes') {
    const controls = <div className={styles.toolbar}><span>API 연동 확인 · 가상 데이터</span>{logoutButton}{sessionError && <span role="alert">{sessionError}</span>}</div>;
    return <Workspace key={selected.id} session={session} currentClass={selected} controls={controls} onClassMenu={openClassSelection} />;
  }
  return <AppShell topBar={<TopBar userName={session.user.displayName} compact actions={logoutButton} />}>
    {sessionError && <p role="alert">{sessionError}</p>}
    <ClassSelectionPage role={session.user.role} classes={data?.items} error={error}
      onSelect={setSelectedClassId} onRetry={retry} />
  </AppShell>;
}

function Workspace({ session, currentClass, controls, onClassMenu }: {
  session: SessionOut;
  currentClass: ClassOut;
  controls: React.ReactNode;
  onClassMenu: () => void;
}) {
  const teacher = session.user.role === 'teacher';
  const [nav, setNav] = useState<NavKey>('dashboard');
  const [area, setArea] = useState<AreaCode>('daily');
  const [uploadRequest, setUploadRequest] = useState(0);
  const [materialId, setMaterialId] = useState<string | null>(null);
  const navigate = (next: NavKey) => {
    setUploadRequest(0);
    setMaterialId(null);
    setNav(next);
  };
  const openMaterial = (id: string) => {
    setMaterialId(id);
    setNav('materials');
  };
  const openPdfUpload = () => {
    setUploadRequest((previous) => previous + 1);
    setNav('materials');
  };
  const { data, error, retry } = useApi(async () => teacher ? { kind: 'teacher' as const, value: await api.getClassDashboard(currentClass.id) } : { kind: 'student' as const, value: await api.getStudentHome(currentClass.id, area) }, [currentClass.id, teacher, area]);
  const bottom = data?.kind === 'teacher' ? <div className={styles.pending}><small>검토 기다리는 문항 묶음</small><b>{data.value.pendingReviewCount}건</b></div> : data?.kind === 'student' ? <div className={styles.myLevel}><small>내 수준 · {data.value.subject}</small>{(['listening', 'reading'] as const).map((skill) => <div key={skill}><span><b>{skill === 'listening' ? '듣기' : '읽기'}</b><em>{data.value.myLevel[skill] ?? '진단 미완료'}</em></span><small>{data.value.myLevel[`${skill}Score`] ?? '–'} / 100점</small></div>)}</div> : undefined;
  return <AppShell topBar={<TopBar userName={session.user.displayName} onLogoClick={() => navigate('dashboard')} />} sidebar={<Sidebar role={session.user.role} className={currentClass.name} active={nav} onNavigate={navigate} onClassMenu={onClassMenu} onCta={teacher ? openPdfUpload : () => navigate('questions')} enabledKeys={teacher ? ['dashboard', 'materials', 'questions'] : ['dashboard', 'materials', 'questions', 'diagnosis']} bottom={bottom} />}>
    {controls}
    {!teacher && nav === 'dashboard' && <label className={styles.areaSelect}>진단 수준을 볼 영역 <select value={area} onChange={(e) => setArea(e.target.value as AreaCode)}>{Object.entries(SUBJECT_LABELS).map(([code, label]) => <option key={code} value={code}>{label}</option>)}</select></label>}
    {!teacher && nav === 'diagnosis' ? <DiagnosticResultPage />
      : !teacher && nav === 'questions' ? <StudentQuizPage classId={currentClass.id} />
      : !teacher && nav === 'materials' ? (materialId
        ? <StudentMaterialView key={materialId} id={materialId} onBack={() => setMaterialId(null)} />
        : <StudentMaterialsPage classId={currentClass.id} onOpen={openMaterial} />)
      : teacher && nav === 'questions' && currentClass.teacherSubject
        ? <QuizMakerPage classId={currentClass.id} subject={currentClass.teacherSubject} />
      : teacher && nav === 'materials' && currentClass.teacherSubject
        ? <PdfMakerPage classId={currentClass.id} subject={currentClass.teacherSubject} openUploadRequest={uploadRequest} />
      : error ? <div role="alert"><p>{error.message}</p><Button onClick={retry}>다시 불러오기</Button></div>
      : !data ? <p role="status">불러오는 중…</p>
      : data.kind === 'teacher' ? <TeacherDashboard data={data.value} />
        : <StudentDashboard data={data.value} onMaterials={() => navigate('materials')} onMaterial={openMaterial} />}
  </AppShell>;
}
