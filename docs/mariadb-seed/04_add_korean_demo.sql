-- Run on a development database already populated by the 가나다계단 seed.
-- Adds the Korean demo and preserves existing accounts, materials and answers.
SET NAMES utf8mb4;

DELIMITER //
BEGIN NOT ATOMIC
    DECLARE demo_student BIGINT UNSIGNED DEFAULT NULL;
    DECLARE main_class BIGINT UNSIGNED DEFAULT NULL;
    DECLARE korean_teacher BIGINT UNSIGNED DEFAULT NULL;
    DECLARE reading_class BIGINT UNSIGNED DEFAULT NULL;
    DECLARE demo_file BIGINT UNSIGNED DEFAULT NULL;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    START TRANSACTION;

    SELECT id INTO demo_student FROM users
    WHERE login_id = 's_a' AND display_name = '학생 A (가상)' AND role = 'student' AND status = 'active'
    FOR UPDATE;

    SET main_class = (
        SELECT c.id
        FROM classes c
        JOIN users owner ON owner.id = c.owner_teacher_id
        JOIN class_memberships member ON member.class_id = c.id AND member.user_id = demo_student
        WHERE owner.login_id = 't_math' AND owner.display_name = 't_math (가상)'
          AND c.name = '가상 3학년 2반' AND c.status = 'active'
          AND member.role = 'student' AND member.status = 'active'
    );

    IF main_class IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Existing 가나다계단 development seed is required.';
    END IF;

    SET korean_teacher = (SELECT id FROM users WHERE login_id = 't_korean');
    IF korean_teacher IS NULL THEN
        INSERT INTO users (login_id, role, password_hash, display_name, native_language_code, teacher_verified_at, status)
        VALUES ('t_korean', 'teacher', 'scrypt-v1$384a309fed9eaa1551ae441eaa4c7c0e$ef37f5d5b2c7e08a08fc26be5675a8bc2ac1b1fb4cd08ae177503c883476b7ee',
                '국어 교사 (가상)', 'ko', UTC_TIMESTAMP(6), 'active');
        SET korean_teacher = LAST_INSERT_ID();
    ELSEIF NOT EXISTS (
        SELECT 1 FROM users WHERE id = korean_teacher AND role = 'teacher' AND display_name = '국어 교사 (가상)'
          AND teacher_verified_at IS NOT NULL AND status = 'active'
    ) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The login t_korean is already used outside the development seed.';
    END IF;

    SET reading_class = (
        SELECT id FROM classes WHERE owner_teacher_id = korean_teacher AND name = '가상 독서반' AND academic_year = 2026
    );
    IF reading_class IS NULL THEN
        INSERT INTO classes (owner_teacher_id, name, grade_label, academic_year, status)
        VALUES (korean_teacher, '가상 독서반', '중3', 2026, 'active');
        SET reading_class = LAST_INSERT_ID();
    END IF;

    INSERT INTO class_memberships (class_id, user_id, role, teacher_subject_code, status)
    SELECT target.class_id, korean_teacher, 'teacher', 'korean', 'active'
    FROM (SELECT main_class AS class_id UNION ALL SELECT reading_class AS class_id) AS target
    WHERE NOT EXISTS (
        SELECT 1 FROM class_memberships m WHERE m.class_id = target.class_id AND m.user_id = korean_teacher
    );

    INSERT INTO class_memberships (class_id, user_id, role, teacher_subject_code, status)
    SELECT reading_class, demo_student, 'student', NULL, 'active'
    WHERE NOT EXISTS (
        SELECT 1 FROM class_memberships m WHERE m.class_id = reading_class AND m.user_id = demo_student
    );

    SET demo_file = (SELECT id FROM files WHERE object_key = 'seed/src_korean_1/sample.pdf');
    IF demo_file IS NULL THEN
        INSERT INTO files (uploaded_by_user_id, object_key, kind, original_name, mime_type, size_bytes, checksum_sha256, scan_status)
        VALUES (korean_teacher, 'seed/src_korean_1/sample.pdf', 'source_pdf', 'sample.pdf', 'application/pdf', 132130, X'd44c0d07d52c535f2fe698c404a79b784c59cc215b43d76435c5be2a5023726d', 'clean');
        SET demo_file = LAST_INSERT_ID();
    ELSEIF NOT EXISTS (
        SELECT 1 FROM files WHERE id = demo_file AND uploaded_by_user_id = korean_teacher
          AND kind = 'source_pdf' AND checksum_sha256 = X'd44c0d07d52c535f2fe698c404a79b784c59cc215b43d76435c5be2a5023726d' AND deleted_at IS NULL
    ) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The Korean demo file key already belongs to another file.';
    END IF;

    INSERT INTO source_materials (class_id, created_by_user_id, source_file_id, title, subject_code, unit, source_text)
    SELECT main_class, korean_teacher, demo_file, '글의 중심 내용 찾기 (가상)', 'korean', '중심 내용 찾기',
           '글의 중심 내용 찾기
글에는 글쓴이가 독자에게 가장 중요하게 전하고 싶은 생각이 있습니다. 이를
글의 중심 내용이라고 합니다.
중심 내용을 찾으려면 먼저 제목을 살펴봅니다. 다음으로 본문에서 반복되는
낱말에 주목합니다. 문단마다 중요한 문장을 찾아 한 문장으로 정리하면 글
전체의 중심 내용을 파악할 수 있습니다.
함께 읽기
우리 반은 점심시간에 책을 서로 바꾸어 읽습니다. 친구가 골라 준 책을 읽다
보면 평소에 읽지 않던 이야기도 만나게 됩니다. 책을 다 읽은 뒤에는 기억에
남는 장면을 친구에게 소개합니다.
윗글의 중심 내용을 한 문장으로 써 봅시다.'
    WHERE NOT EXISTS (
        SELECT 1 FROM source_materials WHERE class_id = main_class AND source_file_id = demo_file
    );

    COMMIT;
END//
DELIMITER ;
