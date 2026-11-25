"""Store one generation job per student in memory or MariaDB."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

from app.core.errors import fail
from app.core.state import Record, new_id, timestamp
from app.database import transaction
from app.generation.solar import ERROR_MESSAGES
from app.mariadb.quizzes import insert_draft
from app.mariadb.store import MariaDBStore, _date

QUIZ_ERRORS = ERROR_MESSAGES | {
    "SOLAR_OUTPUT_INVALID": "출제 계획에 맞는 확인 문제 5문항을 받지 못했습니다. 자료를 확인한 뒤 다시 시도해 주세요.",
    "SOLAR_OUTPUT_TRUNCATED": "Solar 답변이 토큰 한도에서 중단됐습니다. 짧은 원본 지문으로 다시 시도해 주세요.",
    "SOLAR_OUTPUT_EMPTY": "Solar가 최종 문항을 보내지 않았습니다. 다시 시도해 주세요.",
    "SOLAR_OUTPUT_JSON_INVALID": "Solar 답변을 확인 문제 형식으로 읽지 못했습니다. 다시 시도해 주세요.",
    "SOLAR_OUTPUT_FILTERED": "Solar가 답변을 제한했습니다. 출제 지문을 확인해 주세요.",
    "PDF_TEXT_REQUIRED": "문제를 만들 자료 문장이 없습니다.",
    "GENERATION_FAILED": "확인 문제 초안을 저장하지 못했습니다. 자료와 학생 상태를 확인해 주세요.",
    "GENERATION_INPUT_TOO_LARGE": "출제 지문이 너무 깁니다. 짧은 원본 지문을 선택해 주세요.",
}

QUERY = """
SELECT j.*, q.id AS result_id FROM ai_requests j LEFT JOIN quizzes q ON q.ai_request_id = j.id
WHERE j.operation = 'generate_quiz' AND j.prompt_version = 'check-quiz-v1'
"""


def _row(row):
    result = row["result_id"]
    material = row["input_student_material_id"]
    return {
        "id": str(row["id"]), "classId": str(row["class_id"]), "createdBy": str(row["teacher_id"]),
        "sourceId": str(row["source_material_id"]), "studentMemberId": str(row["target_student_member_id"]),
        "inputMaterialId": str(material) if material is not None else None, "credentialId": str(row["credential_id"]),
        "status": row["status"], "model": row["model"], "createdAt": _date(row["created_at"]),
        "quizId": str(result) if result is not None else None, "errorCode": row["safe_error_code"],
        "snapshot": json.loads(row["profile_snapshot"]),
    }


def public(job: Record) -> dict:
    status, code = job["status"], job.get("errorCode")
    started = job["snapshot"].get("startedAt")
    if status == "running" and started and datetime.fromisoformat(started) < datetime.now(timezone.utc) - timedelta(minutes=30):
        status, code = "unknown", "GENERATION_INTERRUPTED"
    return {
        field: job.get(field)
        for field in ("id", "classId", "sourceId", "studentMemberId", "inputMaterialId", "createdBy",
                      "credentialId", "quizId", "createdAt")
    } | {
        "operation": "generate_quiz", "status": status, "errorCode": code, "errorMessage": QUIZ_ERRORS.get(code),
        "resultId": job.get("quizId"), "resultType": "quiz" if job.get("quizId") else None,
    }


def _replay(previous, snapshot):
    if previous[0]["snapshot"]["digest"] != snapshot["digest"]:
        fail(409, "IDEMPOTENCY_CONFLICT", "이미 다른 입력으로 사용한 생성 요청입니다.")
    return previous, False


def batch(store, teacher_id, class_id, candidates):
    snapshot = candidates[0]["snapshot"]
    batch_key = snapshot["batchKey"]
    if isinstance(store, MariaDBStore):
        with transaction() as cursor:
            cursor.execute("SELECT id FROM users WHERE id = %s FOR UPDATE", (teacher_id,))
            cursor.execute(
                QUERY + """ AND j.teacher_id = %s AND j.class_id = %s
                   AND JSON_UNQUOTE(JSON_EXTRACT(j.profile_snapshot,'$.batchKey')) = %s ORDER BY j.id""",
                (teacher_id, class_id, batch_key),
            )
            previous = [_row(row) for row in cursor]
            if previous:
                return _replay(previous, snapshot)
            for job in candidates:
                key = sha256(f"quiz:{class_id}:{batch_key}:{job['studentMemberId']}".encode()).hexdigest()
                cursor.execute(
                    """INSERT INTO ai_requests
                       (teacher_id, class_id, credential_id, source_material_id, target_student_member_id,
                        input_student_material_id, operation, idempotency_key, profile_snapshot, model, prompt_version)
                       VALUES (%s,%s,%s,%s,%s,%s,'generate_quiz',%s,%s,%s,'check-quiz-v1')""",
                    (teacher_id, class_id, job["credentialId"], job["sourceId"], job["studentMemberId"],
                     job["inputMaterialId"], key, json.dumps(job["snapshot"], ensure_ascii=False), job["model"]),
                )
                job.update(id=str(cursor.lastrowid), status="queued", createdAt=timestamp())
    else:
        previous = [
            job for job in store.quiz_jobs.values()
            if job["createdBy"] == teacher_id and job["classId"] == class_id and job["snapshot"]["batchKey"] == batch_key
        ]
        if previous:
            return _replay(previous, snapshot)
        for job in candidates:
            job.update(id=new_id("qjob"), status="queued", createdAt=timestamp())
            store.quiz_jobs[job["id"]] = job
    return candidates, True


def list_owned(store, teacher_id, class_id):
    if isinstance(store, MariaDBStore):
        with transaction() as cursor:
            cursor.execute(QUERY + " AND j.teacher_id = %s AND j.class_id = %s ORDER BY j.id DESC LIMIT 100", (teacher_id, class_id))
            return [_row(row) for row in cursor]
    return [
        job for job in reversed(store.quiz_jobs.values())
        if job["createdBy"] == teacher_id and job["classId"] == class_id
    ][:100]


def get(store, job_id, *, database=False):
    if database or isinstance(store, MariaDBStore):
        with transaction() as cursor:
            cursor.execute(QUERY + " AND j.id = %s", (job_id,))
            row = cursor.fetchone()
            return _row(row) if row else None
    return store.quiz_jobs.get(job_id)


def queued():
    with transaction() as cursor:
        cursor.execute("""SELECT id FROM ai_requests WHERE status='queued' AND operation='generate_quiz'
                          AND prompt_version='check-quiz-v1' ORDER BY id""")
        return [str(row["id"]) for row in cursor]


def claim(store, job_id, database):
    started = timestamp()
    if database:
        with transaction() as cursor:
            cursor.execute(
                """UPDATE ai_requests SET status='running', profile_snapshot=JSON_SET(profile_snapshot,'$.startedAt',%s)
                   WHERE id=%s AND status='queued' AND operation='generate_quiz' AND prompt_version='check-quiz-v1'""",
                (started, job_id),
            )
            acquired = cursor.rowcount == 1
        return get(store, job_id, database=True) if acquired else None
    job = store.quiz_jobs.get(job_id)
    if job and job["status"] == "queued":
        job["status"] = "running"
        job["snapshot"]["startedAt"] = started
        return job
    return None


def succeed(store, job, quiz, database):
    if database:
        with transaction() as cursor:
            cursor.execute("SELECT status FROM ai_requests WHERE id=%s FOR UPDATE", (job["id"],))
            row = cursor.fetchone()
            if not row or row["status"] != "running":
                return
            insert_draft(cursor, quiz, job["id"])
            cursor.execute(
                """UPDATE ai_requests SET status='succeeded', finished_at=UTC_TIMESTAMP(6),
                       profile_snapshot=JSON_SET(profile_snapshot,'$.quizGeneration',JSON_EXTRACT(%s,'$'))
                   WHERE id=%s""",
                (json.dumps(quiz["generation"], ensure_ascii=False), job["id"]),
            )
            cursor.execute(
                """UPDATE ai_credentials SET status='active', last_checked_at=UTC_TIMESTAMP(6)
                   WHERE id=%s AND status='pending'""", (job["credentialId"],),
            )
    else:
        job.update(status="succeeded", quizId=quiz["id"])
        credential = store.ai_credentials.get(job["credentialId"])
        if credential and credential["status"] == "pending":
            credential["status"] = "active"


def failed(store, job_id, code, database):
    status = "unknown" if code in {"SOLAR_TIMEOUT", "GENERATION_INTERRUPTED", "DATABASE_UNAVAILABLE"} else "failed"
    if database:
        with transaction() as cursor:
            cursor.execute(
                """UPDATE ai_requests SET status=%s,safe_error_code=%s,finished_at=UTC_TIMESTAMP(6)
                   WHERE id=%s AND status='running'""", (status, code, job_id),
            )
    else:
        store.quiz_jobs[job_id].update(status=status, errorCode=code)
