"""MariaDB writes for original PDFs and reviewed teaching drafts."""

from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

from app.core.errors import fail
from app.database import transaction

from .files import object_root
from .store import row_version


def save_source(source, file):
    key = f"uploads/{uuid4().hex}.pdf"
    root = object_root(key)
    path = (root / key).resolve()
    if not path.is_relative_to(root):
        fail(503, "FILE_STORAGE_UNAVAILABLE", "서버의 PDF 저장 경로를 확인해 주세요.")
    created = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            created = True
            stream.write(file["data"])
    except OSError:
        if created:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        fail(503, "FILE_STORAGE_UNAVAILABLE", "서버에 PDF를 저장하지 못했습니다.")

    try:
        with transaction() as cursor:
            cursor.execute(
                """SELECT m.id FROM class_memberships m JOIN classes c ON c.id = m.class_id
                   WHERE m.class_id = %s AND m.user_id = %s AND m.teacher_subject_code = %s
                   AND m.role = 'teacher' AND m.status = 'active' AND c.status = 'active' FOR UPDATE""",
                (source["classId"], source["createdBy"], source["subject"]),
            )
            if not cursor.fetchone():
                fail(404, "NOT_FOUND", "담당 교과의 클래스가 아닙니다.")
            cursor.execute(
                """INSERT INTO files
                   (uploaded_by_user_id, object_key, kind, original_name, mime_type, size_bytes, checksum_sha256, scan_status)
                   VALUES (%s, %s, 'source_pdf', %s, 'application/pdf', %s, %s, 'clean')""",
                (source["createdBy"], key, file["name"], file["size"], bytes.fromhex(file["checksum"])),
            )
            cursor.execute(
                """INSERT INTO source_materials
                   (class_id, created_by_user_id, source_file_id, title, subject_code, unit, source_text)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (source["classId"], source["createdBy"], cursor.lastrowid, source["title"],
                 source["subject"], source["unit"], source["sourceText"]),
            )
            source_id = str(cursor.lastrowid)
    except Exception:
        # If commit was acknowledged by MariaDB but the connection broke, leave the object intact.
        # A fresh connection checks whether it is referenced before cleaning up.
        try:
            with transaction() as cursor:
                cursor.execute("SELECT id FROM files WHERE object_key = %s", (key,))
                if cursor.fetchone() is None:
                    path.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    return source | {"id": source_id, "fileId": source_id}


def store_draft(cursor, record, job_id):
    # Serialize revision allocation across workers generating from this source.
    cursor.execute(
        "SELECT id FROM source_materials WHERE id = %s AND archived_at IS NULL FOR UPDATE",
        (record["sourceId"],),
    )
    if cursor.fetchone() is None:
        fail(404, "NOT_FOUND", "등록된 원본 자료가 없습니다.")
    cursor.execute(
        """SELECT COALESCE(MAX(revision), 0) + 1 AS next_revision FROM student_materials
           WHERE source_material_id = %s AND student_member_id = %s""",
        (record["sourceId"], record["studentMemberId"]),
    )
    revision = cursor.fetchone()["next_revision"]
    cursor.execute(
        """INSERT INTO student_materials
           (class_id, source_material_id, student_member_id, revision, ai_request_id,
            created_by_user_id, converted_content, diagnosis_snapshot)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
        (record["classId"], record["sourceId"], record["studentMemberId"], revision, job_id,
         record["createdBy"], _json(record["content"]), _json(record["diagnosisSnapshot"])),
    )
    return str(cursor.lastrowid)


def _json(value):
    return json.dumps(value, ensure_ascii=False)


def _datetime(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc).replace(tzinfo=None) if value else None


def save_revision(record, expected):
    """Keep If-Match and the draft mutation in a single transaction."""
    condition = datetime(1970, 1, 1) + timedelta(microseconds=expected)
    fields = {
        "status": record["status"],
        "reviewed_by_user_id": record.get("reviewedBy"),
        "reviewed_at": _datetime(record["reviewedAt"]),
        "published_by_user_id": record.get("publishedBy"),
        "published_at": _datetime(record["publishedAt"]),
    }
    fields["converted_content"] = _json(record["content"])
    assignments = ", ".join(f"{key} = %s" for key in fields)
    with transaction() as cursor:
        cursor.execute(
            f"""UPDATE student_materials SET {assignments}, updated_at = GREATEST(UTC_TIMESTAMP(6), TIMESTAMPADD(MICROSECOND, 1, updated_at))
                WHERE id = %s AND updated_at = %s""",
            (*fields.values(), record["id"], condition),
        )
        if cursor.rowcount != 1:
            fail(412, "VERSION_CONFLICT", "다른 변경이 있습니다. 다시 조회해 주세요.")
        cursor.execute("SELECT updated_at FROM student_materials WHERE id = %s", (record["id"],))
        updated = cursor.fetchone()["updated_at"]
    record["version"] = row_version(updated)
    record["updatedAt"] = updated.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
