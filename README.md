# 가나다계단 FastAPI 백엔드

교사·학생 로그인부터 클래스, 최초 진단, 자료·문항 검토와 배포, 학습 풀이까지 실행할 수 있는 개발 서버입니다. DB 설정 없이 실행하면 **메모리 모드**로 동작하며 변경 내용은 서버 재시작 시 초기화됩니다. 기존 MariaDB를 쓰려면 아래 Windows 연결 안내를 따르세요.

## VS Code에서 시작

Python 3.12와 VS Code의 Python 및 Python Debugger 확장을 사용합니다. 저장소 루트를 열고 터미널에서 실행합니다.

```bash
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell에서는 활성화 명령 대신 .venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m uvicorn app.main:app --host 127.0.0.1 --port 8081
```

VS Code에서 `Python: Select Interpreter`로 `.venv`의 인터프리터를 선택하면 **가나다계단 FastAPI** 구성으로 F5 디버깅할 수 있습니다. 서버의 `/docs`에서 Swagger UI, `/openapi.json`에서 현재 구현된 계약, `/api/v1/health`에서 실행 상태를 확인할 수 있습니다.

프론트엔드에는 Node.js 22.13 이상을 사용합니다. 백엔드를 8081 포트에서 실행한 상태에서 별도 터미널을 엽니다.

```bash
cd frontend
npm ci
npm run dev
```

Vite는 기본 5173 포트에서 실행되며 `/api` 요청을 백엔드의 8081 포트로 전달합니다. API 접두사는 `/api/v1`입니다. 현재 UI는 교사 대시보드·원본 자료 목록, 학생 대시보드·한국어 진단평가·진단 결과, 로그인·클래스 선택을 제공합니다.

MariaDB DDL·DML, API 명세·mock 서버·테스트, 비교용 프론트엔드 원본, 검토 보고서와 화면 캡처는 [인수인계 자료](docs/handoff/README.md)에 있습니다.

## 가상 계정

모든 가상 계정의 로컬 테스트 비밀번호는 `mock-pass-123`입니다.

| 로그인 ID | 역할 | 확인할 흐름 |
| --- | --- | --- |
| `t_math` | 교사 | `cls_1` 소유자, 수학 자료·문항, 클래스 초대 |
| `t_science` | 교사 | 같은 클래스의 과학 자료·문항 |
| `s_a` | 학생 | 진단 완료, 배포된 자료·문항 조회 및 풀이 |
| `s_b` | 학생 | 진단 완료, 교사 초안과 학생 화면의 노출 차이 |
| `s_pending` | 학생 | 학년군을 선택해 한국어 진단평가 시작, 새로고침 후 이어하기 |
| `s_new` | 학생 | 클래스 초대 수락 후 최초 진단 |
| `t_outside`, `s_outside` | 교사, 학생 | 다른 클래스에 대한 접근 차단 |

## 학생 한국어 진단평가

진단평가 PR이 병합되기 전에는 `feat/korean-assessment`에서 확인할 수 있습니다. ‘진단 화면은 아직 이 프론트엔드에 구현하지 않았습니다’라는 문구가 보이면 VS Code에서 현재 브랜치를 확인해 주세요. 기존 개발 서버를 종료한 뒤 저장소 루트에서 다음을 실행합니다.

```bash
git fetch origin
git switch feat/korean-assessment
git pull --ff-only origin feat/korean-assessment
```

같은 폴더에서 백엔드와 프론트엔드를 다시 켭니다. 백엔드는 VS Code의 **가나다계단 FastAPI**를 실행하고, 별도 PowerShell에서 `cd frontend` 후 `npm.cmd run dev`를 실행합니다.

1. 학생 `s_pending`으로 로그인합니다.
2. 초등 3~4학년 또는 초등 5~6학년을 고릅니다. 각 평가에는 듣기 15문항과 읽기 20문항이 있습니다.
3. 해당 문항에서 **듣기**로 음원을 재생합니다. 답을 고르고 **다음**을 누르거나 **모르겠어요**로 제출합니다. 새로고침하거나 재로그인하면 처음으로 답을 제출하지 않은 문항부터 이어집니다.
4. 결과에서 듣기·읽기 종합과 국어·수학·사회·과학·생활한국어 수준을 확인합니다. 완료 후 학생 사이드바의 **진단 결과**에서 다시 볼 수 있습니다.

음원·이미지는 `frontend/public/diagnostic-assets/{학년군}/{교과 코드}/{listening 또는 reading}/`에서 제공합니다. 문항·정답·배점은 서버의 `app/diagnostic/data/korean_assessment.json`에 있고 React 화면에는 현재 문항의 표시용 데이터만 전달합니다. 첨부 문항의 순서와 배점 기준을 사용하며, 기존 대시보드에는 교과별 환산 점수도 제공합니다. 초등 3~4학년 듣기 5번은 첨부 문항의 선지 텍스트가 비어 있어 지도·음원과 번호 선지를 표시합니다.

화면 제작과 백엔드 점검 항목은 [진단평가 화면 계획](docs/diagnostic-frontend-plan.md)에 정리했습니다.

## 교사 자료 제작

교사로 로그인해 **자료 제작**을 엽니다. 원본 PDF를 등록하거나 기존 원본을 선택하고, 변환할 쪽과 진단을 완료한 학생을 고릅니다. 최대 10쪽을 한 번에 변환합니다. 원본·변환 지면을 비교하면서 초안을 수정하고 검토를 완료한 뒤 학생에게 배포하거나 PDF를 저장할 수 있습니다. 글자가 없는 쪽은 원본으로 표시합니다. 저장한 PDF에는 이미지로 합성한 지면과 핵심어·학습 도움말이 포함됩니다.

실제 모델을 사용할 때는 먼저 백엔드 의존성을 다시 설치합니다. MariaDB를 사용하는 Windows에서는 저장소 루트의 PowerShell에서 실행합니다.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-mariadb.txt
.\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

마지막 명령에서 생성한 값을 로컬 `.env`의 `GANADA_SOLAR_ENCRYPTION_KEY`에 넣고 F5로 백엔드를 다시 실행합니다. 이 값은 교사별 Solar API 키를 암호화하는 서버 키입니다. 각 교사는 자료 제작 화면에서 자신의 Solar API 키를 등록합니다. **변환 시작**을 눌렀을 때 백엔드에서 Solar를 호출합니다. 모델 기본값은 Upstage 모델 ID인 `solar-pro4`입니다. 기존 `.env`에 들어 있는 `solar-pro-4`도 `solar-pro4`로 변환해서 사용합니다. [Upstage의 Solar Pro 4 매개변수 안내](https://github.com/UpstageAI/Solar-Pro4-Cookbook/tree/main/capabilities/parameters)

변환 작업에 실패한 쪽 번호가 나타나면 시작 쪽과 마지막 쪽에 그 번호를 넣어 한 쪽만 변환해 보세요. 토큰 한도로 같은 쪽이 반복해서 실패하면 글이 더 짧은 원본으로 확인하고, JSON이나 문단 검증 오류가 반복되면 변환 작업에 표시된 문구와 쪽 번호를 공유해 주세요. 백엔드 로그에는 작업 ID, 오류 코드, 쪽 번호가 기록됩니다.

이미 MariaDB 전용 계정을 만들었다면 아래 **PDF 제작 권한**을 추가합니다. DB 모드의 원본 업로드는 기본적으로 저장소의 `.ganada-files/`에 저장됩니다. 다른 경로를 써야 한다면 `GANADA_DB_FILES_DIR`을 설정하고, 예시 PDF도 기존 `seed/` 상대 경로에 배치합니다.

[기능 병합 계획](docs/pdf-conversion-plan.md)

## API 사용

로그인 응답의 세션 쿠키를 유지하고, 이후 쓰기 요청에 응답의 `csrfToken`을 `X-CSRF-Token`으로 전달합니다. 문항이나 자료를 수정·검토·배포할 때는 상세 조회나 직전 변경 응답의 `ETag`를 `If-Match`로 보냅니다. 문항 답안을 재전송할 때는 같은 `Idempotency-Key`를 사용합니다. 충돌과 유효성 오류를 포함한 API 오류는 `code`, `message`, `requestId`, `details`로 반환됩니다.

교사 자료·문항과 풀이 이력은 해당 클래스의 담당 교과로 제한합니다. 학생은 진단을 완료한 후 자신에게 배포된 자료와 문항을 볼 수 있습니다. 새 확인 문제를 원본에서 출제하면 학생 화면에는 교사가 선택한 지문만 표시합니다.

현재 PDF 업로드는 텍스트가 포함된 PDF를 10 MiB, 100페이지, 추출 텍스트 25만 자까지 받습니다. 스캔 PDF의 OCR, 파일 악성코드 검사 및 운영용 비공개 파일 저장소는 후속 작업입니다. 아직은 실제 학생 데이터나 운영 계정으로 실행하지 마세요.

전체 기능의 DML 대응, 팀원 기능 연결 지점, 다음 구현 순서는 [백엔드 개발 계획](docs/backend-plan.md)에서 확인할 수 있습니다.

## Windows에서 MariaDB 연결

기존 `ganada`에 예시 데이터 검증까지 마쳤다면 테이블 생성과 예시 데이터 입력 스크립트는 생략합니다. 처음 준비하는 경우 [예시 SQL과 ZIP](docs/mariadb-seed/)을 참고하세요.

`ganada_app` 전용 계정이 없다면 HeidiSQL의 관리자 세션에서 아래 SQL을 한 번 실행합니다. 비밀번호 자리에는 로컬에서 정한 값으로 바꾸세요. 계정이 이미 있으면 `CREATE USER`를 생략합니다.

```sql
CREATE USER 'ganada_app'@'127.0.0.1' IDENTIFIED BY '여기에_새_비밀번호';
GRANT SELECT ON ganada.* TO 'ganada_app'@'127.0.0.1';
GRANT INSERT, UPDATE ON ganada.user_sessions TO 'ganada_app'@'127.0.0.1';
GRANT INSERT, UPDATE ON ganada.student_diagnostics TO 'ganada_app'@'127.0.0.1';
GRANT INSERT ON ganada.diagnostic_answers TO 'ganada_app'@'127.0.0.1';
GRANT INSERT ON ganada.diagnostic_part_results TO 'ganada_app'@'127.0.0.1';
GRANT INSERT ON ganada.diagnostic_area_results TO 'ganada_app'@'127.0.0.1';

-- PDF 제작 권한
GRANT INSERT ON ganada.files TO 'ganada_app'@'127.0.0.1';
GRANT INSERT ON ganada.source_materials TO 'ganada_app'@'127.0.0.1';
GRANT INSERT, UPDATE ON ganada.ai_credentials TO 'ganada_app'@'127.0.0.1';
GRANT INSERT, UPDATE ON ganada.ai_requests TO 'ganada_app'@'127.0.0.1';
GRANT INSERT, UPDATE ON ganada.student_materials TO 'ganada_app'@'127.0.0.1';

-- 확인 문제 권한
GRANT INSERT, UPDATE ON ganada.quizzes TO 'ganada_app'@'127.0.0.1';
GRANT INSERT, UPDATE ON ganada.quiz_questions TO 'ganada_app'@'127.0.0.1';
GRANT INSERT, UPDATE ON ganada.quiz_submissions TO 'ganada_app'@'127.0.0.1';
GRANT INSERT ON ganada.quiz_answer_attempts TO 'ganada_app'@'127.0.0.1';
```

VS Code의 저장소 루트에서 PowerShell로 실행합니다.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-mariadb.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

`.env`에서 `GANADA_DB_HOST=127.0.0.1`, `GANADA_DB_PORT=3306`, `GANADA_DB_NAME=ganada`를 확인합니다. `GANADA_DB_USER`와 `GANADA_DB_PASSWORD`에는 DB 계정과 비밀번호를 넣으세요. 이 비밀번호는 브라우저에서 쓰는 예시 계정 비밀번호와 별개이며, `.env`는 Git에서 제외됩니다. 백엔드를 재시작할 때 F5 구성이 이 파일을 읽습니다.

### 동작 확인

1. F5로 백엔드를 켭니다. `http://127.0.0.1:8081/api/v1/health`에서 `mode: "mariadb"`와 `databaseConnected: true`가 표시되어야 합니다. 시작이 실패하면 터미널의 메시지에서 MariaDB 서비스, 스키마, 계정 권한을 확인하세요.
2. 프론트엔드에서 교사 `t_math`와 `t_science`로 각각 로그인합니다. 두 계정은 같은 반을 보고, 자료 목록은 담당 교과에 해당하는 자료만 보여야 합니다.
3. 학생 `s_pending`으로 로그인해 학년군을 고르고 첫 답안을 제출합니다. F5 백엔드를 중지했다가 다시 켜고 브라우저를 새로 고침합니다. 두 번째 문항부터 열린다면 저장과 이어 풀기가 확인된 것입니다. DB에서 확인하려면 [답안 확인 SQL](docs/mariadb-seed/README.md#앱-연결-후-검사할-흐름)을 실행합니다.

MariaDB에는 세션, 진단평가 진행 결과, PDF 등록과 변환본, 확인 문제 생성·수정·배포와 풀이 이력을 저장합니다. 클래스 생성·초대의 DB 쓰기 API는 501을 반환합니다. DB 접속이 끊기면 메모리로 자동 전환하지 않습니다.

## 교사 문항 생성 · 학생 확인 문제

학생이 공부한 자료로 학생마다 4지선다 확인 문제를 만들고, 교사가 검토해 배포한 뒤 학생이 풉니다. 자료 제작과 같은 교사별 Solar 키를 씁니다.

- **출제 기준**은 4지선다 5문항입니다. 현재 교과 수준과 해당 교과 읽기 수준 중 낮은 쪽을 기준으로 용어·사실·관계·적용·비교 문항을 배분합니다. 기본은 일반 4문항과 도전 1문항입니다.
- **도전 문항 조정 제안**은 같은 학생·교과에서 가장 최근에 최초 풀이를 마친 묶음을 사용합니다. 기본 문항을 2개 이상 틀리면 다음 도전은 0개로 줄이고 용어 문항으로 보강합니다. 도전을 모두 맞히고 기본 오답이 1개 이하면 다음 도전은 2개입니다. 다른 경우는 1개입니다. 첫 풀이 기록을 사용하므로 재풀이 후에도 조정 결과가 유지됩니다. 고급에서는 등급을 더 올리지 않고 같은 수준의 비교·판단 문항을 냅니다.
- **출제 지문**은 배포한 학생용 전환본 또는 교사가 선택한 원본 문장입니다. **선택한 원본 지문**을 고르면 전환본이 있는 학생에게도 선택한 지문을 사용합니다. 교사는 검토에서 문항별 근거 문장을 수정할 수 있습니다.
- **MariaDB**에서는 기존 20개 테이블을 사용합니다. 계정을 이미 만들었다면 위 **확인 문제 권한**을 추가합니다. 출제 계획은 기존 `ai_requests.profile_snapshot`에 기록합니다.

1. 교사 `t_math`로 로그인해 **문항 생성** → **새 문항 만들기**를 누릅니다. Solar 키가 없으면 먼저 저장합니다.
2. 자료와 받을 학생을 고르고 **문항 만들기**를 누릅니다. 완료되면 수준 탭과 문항 묶음 목록에 초안이 나타납니다.
3. 문항 묶음을 고르면 한 문항씩 문제·보기·정답(연한 초록)·해설·근거 문장과 유형·도전 표시를 봅니다. **수정**으로 고친 뒤 **검토 완료** → **학생에게 배포**를 누릅니다.
4. 학생(예: `s_a`)으로 로그인해 **내 문항**에서 풀고, 결과의 **해설 볼래요**로 해설과 근거 문장을, **다시 풀어볼래요**로 틀린 문제만 다시 풉니다.
5. 교사 화면에서 배포한 묶음을 열면 유형별 첫 풀이 결과와 다음 구성 조정을 확인합니다.
