-- HeidiSQL에서 앱이 사용하는 DB를 선택한 뒤 실행합니다.
ALTER TABLE student_diagnostics
  ADD COLUMN IF NOT EXISTS listening_plays JSON NULL COMMENT '문항별 듣기 재생 요청 ID';
