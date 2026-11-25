"""Generation queue with MariaDB and in-memory development storage."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

from app.core.errors import fail
from app.core.state import new_id, timestamp
from app.database import transaction
from app.mariadb.authoring import store_draft
from app.mariadb.store import MariaDBStore

from .solar import ERROR_MESSAGES

QUERY = """
SELECT j.*, m.id AS material_id
FROM ai_requests j LEFT JOIN student_materials m ON m.ai_request_id = j.id
WHERE j.operation = 'convert' AND j.prompt_version = 'pdf-convert-v1'
"""


def _row(row):
    return {
        "id": str(row["id"]), "classId": str(row["class_id"]), "sourceId": str(row["source_material_id"]),
        "studentMemberId": str(row["target_student_member_id"]), "credentialId": str(row["credential_id"]),
        "createdBy": str(row["teacher_id"]), "status": row["status"],
        "model": row["model"], "snapshot": json.loads(row["profile_snapshot"]),
        "resultId": str(row["material_id"]) if row["material_id"] is not None else None,
        "errorCode": row["safe_error_code"],
    }


def public(job):
    status, code = job["status"], job.get("errorCode")
    started = job["snapshot"].get("startedAt")
    if status == "running" and started and datetime.fromisoformat(started) < datetime.now(timezone.utc) - timedelta(minutes=30):
        status, code = "unknown", "GENERATION_INTERRUPTED"
    message = ERROR_MESSAGES.get(code)
    failed_page = job["snapshot"].get("failedPage")
    if message and failed_page is not None:
        message = f"{failed_page}쪽 · {message}"
    return {field: job.get(field) for field in (
        "id", "classId", "sourceId", "studentMemberId", "credentialId", "createdBy", "resultId",
    )} | {
        "status": status, "errorCode": code, "errorMessage": message,
        "resultType": "material" if job.get("resultId") else None,
    }


def request_keys(body, request_key):
    if (not request_key or len(request_key) > 64
            or not request_key.isascii() or not request_key.strip()):
        fail(400, "IDEMPOTENCY_KEY_REQUIRED", "1~64자 Idempotency-Key가 필요합니다.")
    payload = body.model_dump(mode="json")
    payload["studentMemberIds"].sort()
    digest = sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return sha256(request_key.encode()).hexdigest(), digest


def batch(store, teacher_id, class_id, candidates):
    snapshot = candidates[0]["snapshot"]
    batch_key = snapshot["batchKey"]
    if isinstance(store, MariaDBStore):
        with transaction() as cursor:
            cursor.execute("SELECT id FROM users WHERE id = %s FOR UPDATE", (teacher_id,))
            cursor.execute(
                QUERY + """ AND j.teacher_id = %s AND j.class_id = %s
                    AND JSON_UNQUOTE(JSON_EXTRACT(j.profile_snapshot, '$.batchKey')) = %s ORDER BY j.id""",
                (teacher_id, class_id, batch_key),
            )
            previous = [_row(row) for row in cursor]
            if previous:
                return _replay(previous, snapshot), False
            for job in candidates:
                key = sha256(f"{class_id}:{batch_key}:{job['studentMemberId']}".encode()).hexdigest()
                cursor.execute(
                    """INSERT INTO ai_requests
                       (teacher_id, class_id, credential_id, source_material_id, target_student_member_id,
                        operation, idempotency_key, profile_snapshot, model, prompt_version)
                       VALUES (%s,%s,%s,%s,%s,'convert',%s,%s,%s,'pdf-convert-v1')""",
                    (teacher_id, class_id, job["credentialId"], job["sourceId"], job["studentMemberId"],
                     key, json.dumps(job["snapshot"], ensure_ascii=False), job["model"]),
                )
                job["id"] = str(cursor.lastrowid)
    else:
        previous = [
            job for job in store.generation_jobs.values()
            if job["createdBy"] == teacher_id and job["classId"] == class_id and job["snapshot"]["batchKey"] == batch_key
        ]
        if previous:
            return _replay(previous, snapshot), False
        for job in candidates:
            job["id"] = new_id("job")
            store.generation_jobs[job["id"]] = job
    return candidates, True


def _replay(previous, snapshot):
    if previous[0]["snapshot"]["digest"] != snapshot["digest"]:
        fail(409, "IDEMPOTENCY_CONFLICT", "이미 다른 입력으로 사용한 생성 요청입니다.")
    return previous


def list_owned(store, teacher_id, class_id):
    if isinstance(store, MariaDBStore):
        with transaction() as cursor:
            cursor.execute(QUERY + " AND j.teacher_id = %s AND j.class_id = %s ORDER BY j.id DESC LIMIT 100", (teacher_id, class_id))
            return [_row(row) for row in cursor]
    return [
        job for job in reversed(store.generation_jobs.values())
        if job["createdBy"] == teacher_id and job["classId"] == class_id
    ][:100]


def get(store, job_id, *, database=False):
    if database or isinstance(store, MariaDBStore):
        with transaction() as cursor:
            cursor.execute(QUERY + " AND j.id = %s", (job_id,))
            row = cursor.fetchone()
            return _row(row) if row else None
    return store.generation_jobs.get(job_id)


def queued():
    with transaction() as cursor:
        cursor.execute(
            """SELECT id FROM ai_requests WHERE status = 'queued' AND operation = 'convert'
               AND prompt_version = 'pdf-convert-v1' ORDER BY id"""
        )
        return [str(row["id"]) for row in cursor]


def claim(store, job_id, database):
    started_at = timestamp()
    if database:
        with transaction() as cursor:
            cursor.execute(
                """UPDATE ai_requests SET status = 'running',
                   profile_snapshot = JSON_SET(profile_snapshot, '$.startedAt', %s)
                   WHERE id = %s AND status = 'queued' AND operation = 'convert' AND prompt_version = 'pdf-convert-v1'""",
                (started_at, job_id),
            )
            acquired = cursor.rowcount == 1
        return get(store, job_id, database=True) if acquired else None
    job = store.generation_jobs.get(job_id)
    if job and job["status"] == "queued":
        job["status"] = "running"
        job["snapshot"]["startedAt"] = started_at
        return job
    return None


def succeed(store, job, draft, database):
    if database:
        with transaction() as cursor:
            cursor.execute("SELECT status FROM ai_requests WHERE id = %s FOR UPDATE", (job["id"],))
            if cursor.fetchone()["status"] != "running":
                return
            result_id = store_draft(cursor, draft, job["id"])
            cursor.execute(
                """UPDATE ai_requests SET status = 'succeeded', finished_at = UTC_TIMESTAMP(6)
                   WHERE id = %s""",
                (job["id"],),
            )
            cursor.execute(
                """UPDATE ai_credentials SET status = 'active', last_checked_at = UTC_TIMESTAMP(6)
                   WHERE id = %s AND status = 'pending'""",
                (job["credentialId"],),
            )
    else:
        job.update(status="succeeded", resultId=draft["id"])
        credential = store.ai_credentials.get(job["credentialId"])
        if credential and credential["status"] == "pending":
            credential["status"] = "active"


def failed(store, job_id, code, database, page=None):
    status = "unknown" if code in {"SOLAR_TIMEOUT", "DATABASE_UNAVAILABLE", "GENERATION_INTERRUPTED"} else "failed"
    if database:
        with transaction() as cursor:
            cursor.execute(
                """UPDATE ai_requests SET status = %s, safe_error_code = %s, finished_at = UTC_TIMESTAMP(6),
                   profile_snapshot = CASE WHEN %s IS NULL THEN profile_snapshot
                                           ELSE JSON_SET(profile_snapshot, '$.failedPage', %s) END
                   WHERE id = %s AND status = 'running'""",
                (status, code, page, page, job_id),
            )
    else:
        job = store.generation_jobs[job_id]
        job.update(status=status, errorCode=code)
        if page is not None:
            job["snapshot"]["failedPage"] = page
