# 가나다계단 개발 인계 자료

이 폴더는 2026-11-21 대화에서 작성한 DB 설계, API 1.1 mock, 첨부 프론트엔드의 연결 수정본입니다. 저장소 루트의 기존 앱과 별도로 실행합니다. 실제 DB·Solar 운영 연결은 아직 구현되지 않았습니다.

## 저장소에서 실행하기

현재 FastAPI 백엔드와 연결된 프론트엔드는 [저장소 실행 안내](../../README.md)를 참고하세요. 여기에 보관한 명세, mock, 검토 보고서와 검증 결과는 전달 시점의 자료입니다. mock을 별도로 실행하려면 [mock 안내](api/README.md)를 참고하세요. FastAPI 백엔드와 포트가 같으므로 한 번에 하나를 실행합니다.

## 자료 위치

- `database-ddl/`: MariaDB 11.8용 20개 테이블 DDL과 검증 기록
- `database-dml/`: 기능별 DML 설계와 검증 기록
- `api/baseline/`: API 설계 기준 DDL/DML 스냅샷
- `api/`: 수정된 mock 서버, OpenAPI·HTML·Markdown 명세, 응답 예시, 테스트
- [저장소 루트의 `frontend/`](../../frontend/): 실제 HTTP mock에 연결한 React/TypeScript/Vite 수정본
- `frontend-original/`: 비교용 첨부 원본
- `FRONTEND_API_REVIEW.md`, `API_CHANGES.md`: 검토 근거와 변경 사항
- `DEVELOPMENT_PLAN.md`: 실제 서비스까지 남은 개발 순서
- `VERIFICATION.md`, `browser-results.json`, `screenshots/`: 검증 결과
- `시작하기.md`: Windows 실행 방법

가상 계정만 포함합니다. `.env`, 실제 API 키, node_modules는 포함하지 않습니다. API mock을 재시작하면 가상 변경 데이터가 초기화됩니다.
