# 가나다계단 MariaDB 개발용 예시 데이터

MariaDB 11.8.6에서 기존 20개 테이블 DDL과 함께 확인했습니다. 가나다계단 앱의 가상 계정·클래스·교과 배정을 유지합니다. 개발용 예시 DB에만 넣으세요.

## HeidiSQL로 넣기

1. SQL 탭에서 `SELECT VERSION(), DATABASE();`와 `SHOW TABLES FROM ganada;`를 실행해 서버와 스키마를 확인하세요. DB 이름이 다르면 `SHOW TABLES FROM` 뒤에 실제 이름을 입력합니다.
2. 테이블이 없을 때만 `01_create_tables.sql`을 실행합니다. 이 파일은 원본 DDL이며 `ganada` 스키마를 지정합니다. 다른 이름의 빈 스키마를 쓸 경우 실행 전에 파일의 스키마 이름을 맞춥니다. 테이블이 이미 20개 있는 경우에는 이 단계를 건너뜁니다.
3. 왼쪽 트리에서 대상 개발 DB를 선택하세요. 새 쿼리 탭에 `02_seed_data.sql`을 UTF-8로 열고, `DELIMITER`를 포함해 파일 전체를 실행합니다. 데이터가 있는 `users`나 `diagnostic_forms`를 발견하면 중단합니다. 삽입 중 오류가 나면 트랜잭션 전체를 취소합니다.
4. 같은 DB에서 `03_verify_data.sql`을 실행합니다. 초기 데이터라면 `actual_rows`와 `expected_rows`가 같아야 합니다. 계정별 반 배정과 문항집별 문항 수도 아래 결과로 확인할 수 있습니다.

HeidiSQL에서 여러 문장 구분이 문제라면 MariaDB CLI로 같은 스키마에 접속하고 `SOURCE C:/경로/02_seed_data.sql`을 실행합니다. CLI 접속 시 `--default-character-set=utf8mb4`를 사용할 수 있습니다.

## 예시 계정

앱의 가상 로그인 비밀번호는 모두 `mock-pass-123`입니다. SQL의 `users.password_hash`에는 현재 앱의 `scrypt-v1` 구현으로 변환한 값만 들어 있습니다. 계정마다 salt가 다릅니다. 이 비밀번호는 HeidiSQL이나 MariaDB 접속 계정의 비밀번호와 별개입니다.

| 역할 | 계정 | 예시 흐름 |
| --- | --- | --- |
| 교사 | `t_math`, `t_science` | 같은 반, 담당 교과는 수학과 과학 |
| 교사 | `t_outside` | 다른 반의 수학 교사 |
| 학생 | `s_a`, `s_b` | 같은 반의 진단 완료 학생 |
| 학생 | `s_pending` | 같은 반의 미완료 학생, 학년군을 선택하고 진단 시작 |
| 학생 | `s_new` | 아직 클래스에 소속되지 않음 |
| 학생 | `s_outside` | 다른 반의 진단 완료 학생 |

클래스 2개, 소속 7개, 원본 자료 3개, 변환 자료 3개, 문제 묶음 4개가 들어 있습니다. 진단 완료 상태 재현용 가상 문항집 1개와 첨부 3~4학년·5~6학년 문항집 2개, 완료 학생 3명의 예시 답안과 결과를 포함합니다. 세션과 AI 접속 키는 앱이 필요할 때 처리합니다.

SQL의 파일 행은 메타데이터입니다. 예시 PDF는 ZIP의 `files/` 아래 `object_key`와 같은 상대 경로에 있습니다. 진단 음원과 이미지는 이미 구현 브랜치의 `frontend/public/`에 있으며 해당 상대 경로를 참조합니다. 파일 다운로드의 실제 저장소 경로는 앱 연결 단계에서 설정합니다. 진단 정답·비밀번호 해시가 포함되므로 SQL은 프론트엔드 공개 폴더에 두지 마세요.

## 앱 연결 후 검사할 흐름

1. 백엔드를 MariaDB 설정으로 시작하고 해당 스키마에 연결되었는지 확인합니다.
2. 브라우저에서 `t_math`와 `t_science`로 각각 로그인했을 때 같은 반을 보되 담당 교과 자료만 보여야 합니다.
3. `s_a`는 진단 결과와 배포된 자료를 볼 수 있어야 합니다. `s_pending`은 학년군을 선택해 진단을 시작할 수 있어야 합니다.
4. `s_pending`으로 답안 한 개를 제출한 뒤 백엔드를 재시작하고 다시 로그인합니다. 다음 문항부터 이어 풀리면 MariaDB 저장을 확인할 수 있습니다. HeidiSQL에서 다음 쿼리로도 확인할 수 있습니다.

```sql
SELECT u.login_id, d.status, COUNT(a.question_id) AS submitted_answers
FROM users u
JOIN student_diagnostics d ON d.student_id = u.id
LEFT JOIN diagnostic_answers a ON a.student_id = d.student_id
WHERE u.login_id = 's_pending'
GROUP BY u.login_id, d.status;
```

위 앱 검사는 MariaDB 연결 코드가 적용된 뒤에 가능합니다. SQL을 가져온 상태에서는 기존 개발 서버가 현재 메모리 데이터로 동작합니다.
