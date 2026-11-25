"""Transactional writes for generated quizzes, teacher review and student attempts."""

from datetime import datetime, timedelta
import json

from app.core.errors import fail
from app.database import transaction
from app.quizzes.dto import AnswerOut
from app.quizzes.service import quiz_results, submit_answer

from .store import _date, row_version


def _question_values(question):
    return (question["prompt"], json.dumps(question["choices"], ensure_ascii=False), question["correctOptionId"],
            question["explanation"], json.dumps(question["sourceSentenceIds"]), question["maxPoints"])


def insert_draft(cursor, quiz, job_id):
    cursor.execute(
        """SELECT s.id FROM source_materials s JOIN classes c ON c.id = s.class_id AND c.status = 'active'
           JOIN class_memberships t ON t.class_id = s.class_id AND t.user_id = %s
                AND t.teacher_subject_code = s.subject_code AND t.status = 'active' AND t.role = 'teacher'
           JOIN users u ON u.id = t.user_id AND u.status = 'active' AND u.teacher_verified_at IS NOT NULL
           JOIN class_memberships m ON m.id = %s AND m.class_id = s.class_id
                AND m.status = 'active' AND m.role = 'student'
           JOIN users student ON student.id = m.user_id AND student.status = 'active'
           JOIN student_diagnostics d ON d.student_id = student.id AND d.status = 'completed'
           LEFT JOIN student_materials sm ON sm.id = %s
           WHERE s.id = %s AND s.archived_at IS NULL
             AND (%s IS NULL OR (sm.status = 'published' AND sm.student_member_id = m.id
                                  AND sm.source_material_id = s.id)) FOR UPDATE""",
        (quiz["createdBy"], quiz["studentMemberId"], quiz["inputMaterialId"], quiz["sourceId"], quiz["inputMaterialId"]),
    )
    if cursor.fetchone() is None:
        fail(409, "SOURCE_CHANGED", "원본 자료 또는 대상 학생에 대한 접근이 변경되었습니다.")
    cursor.execute(
        """SELECT COALESCE(MAX(generation_no), 0) + 1 AS next_no FROM quizzes
           WHERE source_material_id = %s AND student_member_id = %s""",
        (quiz["sourceId"], quiz["studentMemberId"]),
    )
    number = cursor.fetchone()["next_no"]
    cursor.execute(
        """INSERT INTO quizzes
           (class_id, source_material_id, student_member_id, student_material_id, ai_request_id,
            created_by_user_id, title, generation_no, question_type, source_hash)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (quiz["classId"], quiz["sourceId"], quiz["studentMemberId"], quiz["inputMaterialId"], job_id,
         quiz["createdBy"], quiz["title"], number, quiz["questionType"], bytes.fromhex(quiz["sourceHash"])),
    )
    quiz_id = str(cursor.lastrowid)
    cursor.executemany(
        """INSERT INTO quiz_questions
           (quiz_id, position, question_type, prompt, choices, correct_option_id,
            explanation, source_sentence_ids, max_points) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        [(quiz_id, row["position"], quiz["questionType"], *_question_values(row)) for row in quiz["questions"]],
    )
    return quiz_id


def save_revision(quiz, actor_id, expected, question=None):
    condition = datetime(1970, 1, 1) + timedelta(microseconds=expected)
    with transaction() as cursor:
        cursor.execute(
            """UPDATE quizzes q
               JOIN classes c ON c.id=q.class_id AND c.status='active'
               JOIN class_memberships m ON m.id = q.student_member_id AND m.status = 'active'
               JOIN users u ON u.id = m.user_id AND u.status = 'active'
               JOIN source_materials s ON s.id = q.source_material_id AND s.archived_at IS NULL
               JOIN class_memberships t ON t.class_id=q.class_id AND t.user_id=%s
                   AND t.role='teacher' AND t.status='active' AND t.teacher_subject_code=s.subject_code
               JOIN users teacher ON teacher.id=t.user_id AND teacher.status='active' AND teacher.teacher_verified_at IS NOT NULL
               LEFT JOIN student_materials sm ON sm.id = q.student_material_id
               SET q.status = %s, q.reviewed_by_user_id = %s,
                   q.reviewed_at = CASE WHEN %s = 'draft' THEN NULL
                                        WHEN %s = 'reviewed' THEN UTC_TIMESTAMP(6) ELSE q.reviewed_at END,
                   q.published_by_user_id = %s,
                   q.published_at = CASE WHEN %s = 'published' THEN UTC_TIMESTAMP(6) ELSE NULL END,
                   q.updated_at = GREATEST(UTC_TIMESTAMP(6), TIMESTAMPADD(MICROSECOND, 1, q.updated_at))
               WHERE q.id = %s AND q.updated_at = %s AND q.published_at IS NULL
                 AND t.user_id IN (q.created_by_user_id, c.owner_teacher_id)
                 AND (q.student_material_id IS NULL OR sm.status = 'published')""",
            (actor_id, quiz["status"], quiz.get("reviewedBy"), quiz["status"], quiz["status"],
             quiz.get("publishedBy"), quiz["status"], quiz["id"], condition),
        )
        if cursor.rowcount != 1:
            fail(412, "VERSION_CONFLICT", "자료나 문제 묶음이 변경됐습니다. 다시 조회해 주세요.")
        if question is not None:
            cursor.execute(
                """UPDATE quiz_questions SET prompt=%s, choices=%s, correct_option_id=%s,
                   explanation=%s, source_sentence_ids=%s, max_points=%s WHERE id=%s AND quiz_id=%s""",
                (*_question_values(question), question["id"], quiz["id"]),
            )
        cursor.execute("SELECT updated_at, reviewed_at, published_at FROM quizzes WHERE id = %s", (quiz["id"],))
        dates = cursor.fetchone()
    quiz.update(version=row_version(dates["updated_at"]), updatedAt=_date(dates["updated_at"]),
                reviewedAt=_date(dates["reviewed_at"]), publishedAt=_date(dates["published_at"]))


def save_answer(store, quiz, user, body, request_key):
    if not request_key or not request_key.strip() or len(request_key) > 64 or not request_key.isascii():
        fail(400, "IDEMPOTENCY_KEY_REQUIRED", "1~64자 Idempotency-Key가 필요합니다.")
    quiz_id = quiz["id"]
    with transaction() as cursor:
        cursor.execute(
            """SELECT q.id FROM quizzes q JOIN classes c ON c.id=q.class_id AND c.status='active'
               JOIN source_materials s ON s.id=q.source_material_id AND s.archived_at IS NULL
               JOIN class_memberships m ON m.id=q.student_member_id AND m.class_id=q.class_id
                   AND m.user_id=%s AND m.role='student' AND m.status='active'
               JOIN users u ON u.id=m.user_id AND u.status='active'
               JOIN student_diagnostics d ON d.student_id=u.id AND d.status='completed'
               LEFT JOIN student_materials sm ON sm.id=q.student_material_id
               WHERE q.id=%s AND q.status='published'
                 AND (q.student_material_id IS NULL OR sm.status='published') FOR UPDATE""",
            (user["id"], quiz_id),
        )
        if cursor.fetchone() is None:
            fail(404, "NOT_FOUND", "문제 묶음을 찾을 수 없습니다.")
        cursor.execute(
            """SELECT *, request_key=%s AS same_request FROM quiz_answer_attempts WHERE quiz_id=%s
               ORDER BY question_id, attempt_no""", (request_key, quiz_id),
        )
        attempts = {}
        previous = None
        for row in cursor:
            qid = str(row["question_id"])
            attempts.setdefault(qid, []).append({
                "isCorrect": bool(row["is_correct"]), "points": float(row["points"]),
                "answeredAt": _date(row["answered_at"]),
            })
            if row["same_request"]:
                previous = row
        store.attempts[quiz_id] = attempts
        if previous:
            if (str(previous["question_id"]), previous["selected_option_id"]) != (body.questionId, body.selectedOptionId):
                fail(409, "IDEMPOTENCY_CONFLICT", "같은 요청키로 다른 내용을 보낼 수 없습니다.")
            return AnswerOut(
                questionId=body.questionId, attemptNo=previous["attempt_no"], isCorrect=bool(previous["is_correct"]),
                points=float(previous["points"]), retryCount=previous["attempt_no"] - 1, replayed=True,
                results=quiz_results(store, quiz),
            )

        result = submit_answer(store, quiz, user, body, request_key)
        cursor.execute(
            """INSERT INTO quiz_submissions (quiz_id, max_score) VALUES (%s,%s)
               ON DUPLICATE KEY UPDATE quiz_id=VALUES(quiz_id)""", (quiz_id, result.results.maxScore),
        )
        cursor.execute(
            """INSERT INTO quiz_answer_attempts
               (quiz_id, question_id, attempt_no, selected_option_id, is_correct, points, request_key)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (quiz_id, body.questionId, result.attemptNo, body.selectedOptionId, result.isCorrect, result.points, request_key),
        )
        if result.results.status == "completed":
            cursor.execute(
                """UPDATE quiz_submissions SET status='completed', first_score=%s, first_submitted_at=UTC_TIMESTAMP(6)
                   WHERE quiz_id=%s AND status='in_progress'""", (result.results.firstScore, quiz_id),
            )
        return result
