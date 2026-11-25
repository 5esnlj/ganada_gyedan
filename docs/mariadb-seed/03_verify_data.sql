SET NAMES utf8mb4;
SELECT DATABASE() AS selected_database, VERSION() AS mariadb_version;
SELECT 'users' AS table_name, COUNT(*) AS actual_rows, 9 AS expected_rows FROM users
UNION ALL
SELECT 'classes' AS table_name, COUNT(*) AS actual_rows, 3 AS expected_rows FROM classes
UNION ALL
SELECT 'class_memberships' AS table_name, COUNT(*) AS actual_rows, 10 AS expected_rows FROM class_memberships
UNION ALL
SELECT 'files' AS table_name, COUNT(*) AS actual_rows, 57 AS expected_rows FROM files
UNION ALL
SELECT 'source_materials' AS table_name, COUNT(*) AS actual_rows, 4 AS expected_rows FROM source_materials
UNION ALL
SELECT 'diagnostic_forms' AS table_name, COUNT(*) AS actual_rows, 3 AS expected_rows FROM diagnostic_forms
UNION ALL
SELECT 'diagnostic_questions' AS table_name, COUNT(*) AS actual_rows, 140 AS expected_rows FROM diagnostic_questions
UNION ALL
SELECT 'student_diagnostics' AS table_name, COUNT(*) AS actual_rows, 3 AS expected_rows FROM student_diagnostics
UNION ALL
SELECT 'diagnostic_answers' AS table_name, COUNT(*) AS actual_rows, 210 AS expected_rows FROM diagnostic_answers
UNION ALL
SELECT 'diagnostic_part_results' AS table_name, COUNT(*) AS actual_rows, 30 AS expected_rows FROM diagnostic_part_results
UNION ALL
SELECT 'diagnostic_area_results' AS table_name, COUNT(*) AS actual_rows, 15 AS expected_rows FROM diagnostic_area_results
UNION ALL
SELECT 'student_materials' AS table_name, COUNT(*) AS actual_rows, 3 AS expected_rows FROM student_materials
UNION ALL
SELECT 'quizzes' AS table_name, COUNT(*) AS actual_rows, 4 AS expected_rows FROM quizzes
UNION ALL
SELECT 'quiz_questions' AS table_name, COUNT(*) AS actual_rows, 12 AS expected_rows FROM quiz_questions;

SELECT u.login_id, u.role, u.display_name, c.name AS class_name, m.teacher_subject_code,
       d.status AS diagnostic_status
FROM users u
LEFT JOIN class_memberships m ON m.user_id = u.id
LEFT JOIN classes c ON c.id = m.class_id
LEFT JOIN student_diagnostics d ON d.student_id = u.id
ORDER BY u.id;

SELECT f.version, COUNT(q.id) AS question_count
FROM diagnostic_forms f JOIN diagnostic_questions q ON q.form_id = f.id
GROUP BY f.id, f.version
ORDER BY f.id;
