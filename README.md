# 가나다계단 FastAPI 백엔드

교사·학생 로그인부터 클래스, 최초 진단, 자료·문항 검토와 배포, 학습 풀이까지 실행할 수 있는 개발 서버입니다. 기본 실행은 **가상 계정과 자료를 사용하는 단일 프로세스 메모리 모드**입니다. 세션, 업로드, 초안, 답안과 진단 진행은 서버를 재시작하면 초기화됩니다.

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

백엔드를 8081 포트에서 실행한 상태에서 별도 터미널을 엽니다.

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

## API 사용

로그인 응답의 세션 쿠키를 유지하고, 이후 쓰기 요청에 응답의 `csrfToken`을 `X-CSRF-Token`으로 전달합니다. 문항이나 자료를 수정·검토·배포할 때는 상세 조회나 직전 변경 응답의 `ETag`를 `If-Match`로 보냅니다. 문항 답안을 재전송할 때는 같은 `Idempotency-Key`를 사용합니다. 충돌과 유효성 오류를 포함한 API 오류는 `code`, `message`, `requestId`, `details`로 반환됩니다.

교사 자료·문항과 풀이 이력은 해당 클래스의 담당 교과로 제한합니다. 학생은 진단을 완료한 후 자신에게 배포된 자료와 문항을 볼 수 있습니다. 원본 PDF는 교사가 접근할 수 있는 등록 원본 또는 학생에게 배포된 원본 직접 출제 문항을 통해 내려받습니다.

현재 PDF 업로드는 텍스트가 포함된 PDF를 10 MiB, 100페이지, 추출 텍스트 25만 자까지 받습니다. 스캔 PDF의 OCR, 파일 악성코드 검사 및 운영용 비공개 파일 저장소는 후속 작업입니다. 아직은 실제 학생 데이터나 운영 계정으로 실행하지 마세요.

전체 기능의 DML 대응, 팀원 기능 연결 지점, 다음 구현 순서는 [백엔드 개발 계획](docs/backend-plan.md)에서 확인할 수 있습니다.
