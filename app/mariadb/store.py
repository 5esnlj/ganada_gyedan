"""Project development database rows into the existing API service models."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json

from app.core.state import MemoryStore
from app.database import transaction
from app.diagnostic.forms import DiagnosticForm
from app.materials.pdf import source_sentences

from .diagnostics import load_diagnostics, load_forms
from .sessions import load_session


@dataclass
class MariaDBStore(MemoryStore):
    forms: dict[int, DiagnosticForm] = field(default_factory=dict)
    assessment_forms: dict[str, DiagnosticForm] = field(default_factory=dict)
    legacy_form: DiagnosticForm | None = None


def _date(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _id(value):
    return str(value) if value is not None else None


def load_store(token: str | None, *, include_learning: bool = False) -> MariaDBStore:
    store = MariaDBStore()
    with transaction() as cursor:
        cursor.execute("""SELECT id, login_id, role, display_name, native_language_code, status,
                                 teacher_verified_at FROM users ORDER BY id""")
        for row in cursor:
            key = str(row["id"])
            store.users[key] = {
                "id": key, "loginId": row["login_id"], "role": row["role"], "displayName": row["display_name"],
                "nativeLanguage": row["native_language_code"] or "", "status": row["status"],
                "verified": row["teacher_verified_at"] is not None,
            }
        cursor.execute("SELECT * FROM classes ORDER BY id")
        for row in cursor:
            key = str(row["id"])
            store.classes[key] = {
                "id": key, "ownerId": str(row["owner_teacher_id"]), "name": row["name"],
                "gradeLabel": row["grade_label"], "academicYear": row["academic_year"], "status": row["status"],
            }
        cursor.execute("SELECT * FROM class_memberships ORDER BY id")
        for row in cursor:
            key = str(row["id"])
            store.memberships[key] = {
                "id": key, "classId": str(row["class_id"]), "userId": str(row["user_id"]), "role": row["role"],
                "teacherSubject": row["teacher_subject_code"], "status": row["status"],
                "rosterNumber": row["roster_number"],
            }
        load_session(cursor, store, token)
        load_forms(cursor, store)
        load_diagnostics(cursor, store)
        if store.sessions or include_learning:
            _load_learning_content(cursor, store)
    return store


def _load_learning_content(cursor, store):
    cursor.execute(
        """SELECT s.*, f.object_key, f.original_name, f.mime_type, f.size_bytes, f.checksum_sha256
           FROM source_materials s LEFT JOIN files f ON f.id = s.source_file_id
           AND f.deleted_at IS NULL AND f.scan_status = 'clean' AND f.kind = 'source_pdf'"""
    )
    for row in cursor:
        key = str(row["id"])
        location = {
            "classId": str(row["class_id"]), "subject": row["subject_code"], "createdBy": str(row["created_by_user_id"]),
        }
        file_id = None
        if row["object_key"] is not None:
            # Authorize a source reference, even if several sources share the same file row.
            file_id = key
            store.files[file_id] = dict(
                location, id=file_id, objectKey=row["object_key"], name=row["original_name"],
                mimeType=row["mime_type"], size=row["size_bytes"], checksum=row["checksum_sha256"],
            )
        store.sources[key] = dict(
            location, id=key, fileId=file_id, title=row["title"], unit=row["unit"],
            sourceText=row["source_text"], sentences=source_sentences(row["source_text"]),
            archived=row["archived_at"] is not None, createdAt=_date(row["created_at"]),
        )

    cursor.execute("SELECT * FROM student_materials")
    for row in cursor:
        key = str(row["id"])
        store.materials[key] = _assignment(store, row) | {
            "id": key, "revision": row["revision"], "title": store.sources[str(row["source_material_id"])]["title"],
            "content": json.loads(row["converted_content"]),
        }

    cursor.execute("SELECT * FROM quizzes")
    for row in cursor:
        key = str(row["id"])
        store.quizzes[key] = _assignment(store, row) | {
            "id": key, "title": row["title"], "generationNo": row["generation_no"],
            "questionType": row["question_type"], "inputMaterialId": _id(row["student_material_id"]), "questions": [],
        }
    cursor.execute("SELECT * FROM quiz_questions ORDER BY quiz_id, position")
    for row in cursor:
        store.quizzes[str(row["quiz_id"])]["questions"].append({
            "id": str(row["id"]), "position": row["position"], "questionType": row["question_type"],
            "prompt": row["prompt"], "choices": json.loads(row["choices"]), "correctOptionId": row["correct_option_id"],
            "explanation": row["explanation"], "sourceSentenceIds": json.loads(row["source_sentence_ids"]),
            "maxPoints": float(row["max_points"]),
        })
    cursor.execute("SELECT * FROM quiz_answer_attempts ORDER BY quiz_id, question_id, attempt_no")
    for row in cursor:
        quiz = store.attempts.setdefault(str(row["quiz_id"]), {})
        quiz.setdefault(str(row["question_id"]), []).append({
            "isCorrect": bool(row["is_correct"]), "points": float(row["points"]), "answeredAt": _date(row["answered_at"]),
        })


def row_version(updated):
    delta = updated - datetime(1970, 1, 1)
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def _assignment(store, row):
    source_id = str(row["source_material_id"])
    updated = row["updated_at"]
    return {
        "classId": str(row["class_id"]), "sourceId": source_id, "studentMemberId": str(row["student_member_id"]),
        "subject": store.sources[source_id]["subject"], "status": row["status"], "version": row_version(updated),
        "createdBy": str(row["created_by_user_id"]), "createdAt": _date(row["created_at"]),
        "updatedAt": _date(updated), "publishedAt": _date(row["published_at"]), "reviewedAt": _date(row["reviewed_at"]),
        "publishedBy": _id(row["published_by_user_id"]), "reviewedBy": _id(row["reviewed_by_user_id"]),
    }
