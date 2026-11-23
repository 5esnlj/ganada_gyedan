"""Teacher-owned Solar credentials. Only encrypted values enter storage."""

import os

from cryptography.fernet import Fernet, InvalidToken

from app.core.errors import fail
from app.core.state import new_id
from app.database import transaction
from app.mariadb.store import MariaDBStore

from .solar import ERROR_MESSAGES


def _cipher() -> Fernet:
    try:
        return Fernet(os.environ["GANADA_SOLAR_ENCRYPTION_KEY"].encode())
    except (KeyError, ValueError):
        fail(503, "SOLAR_ENCRYPTION_REQUIRED", ERROR_MESSAGES["SOLAR_ENCRYPTION_REQUIRED"])


def rows(store, teacher_id):
    if isinstance(store, MariaDBStore):
        with transaction() as cursor:
            cursor.execute(
                """SELECT id, teacher_id, label, masked_suffix, status
                   FROM ai_credentials WHERE teacher_id = %s AND status IN ('pending','active') ORDER BY id DESC""",
                (teacher_id,),
            )
            return [
                dict(id=str(row["id"]), teacherId=str(row["teacher_id"]), label=row["label"],
                     maskedSuffix=row["masked_suffix"], status=row["status"])
                for row in cursor
            ]
    return [
        {field: row[field] for field in ("id", "teacherId", "label", "maskedSuffix", "status")}
        for row in store.ai_credentials.values()
        if row["teacherId"] == teacher_id and row["status"] in ("pending", "active")
    ]


def register(store, teacher_id, body):
    api_key = body.apiKey.get_secret_value().strip()
    if len(api_key) < 8 or not api_key.isascii() or any(c.isspace() or not c.isprintable() for c in api_key):
        fail(422, "INVALID_SOLAR_KEY", "공백 없이 Solar API 키를 입력해 주세요.")
    encoded = _cipher().encrypt(api_key.encode())
    row = {"teacherId": teacher_id, "label": body.label.strip(), "maskedSuffix": api_key[-4:], "status": "pending"}
    if isinstance(store, MariaDBStore):
        with transaction() as cursor:
            cursor.execute("SELECT id FROM users WHERE id = %s FOR UPDATE", (teacher_id,))
            cursor.execute(
                """UPDATE ai_credentials SET status = 'revoked', revoked_at = UTC_TIMESTAMP(6), encrypted_secret = ''
                   WHERE teacher_id = %s AND status <> 'revoked'""",
                (teacher_id,),
            )
            cursor.execute(
                """INSERT INTO ai_credentials (teacher_id, label, encrypted_secret, key_version, masked_suffix)
                   VALUES (%s,%s,%s,'fernet-v1',%s)""",
                (teacher_id, row["label"], encoded, row["maskedSuffix"]),
            )
            row["id"] = str(cursor.lastrowid)
    else:
        for old in store.ai_credentials.values():
            if old["teacherId"] == teacher_id:
                old.update(status="revoked", encoded=b"")
        row["id"] = new_id("cred")
        store.ai_credentials[row["id"]] = row | {"encoded": encoded}
    return row


def unlock(store, teacher_id, credential_id):
    if isinstance(store, MariaDBStore):
        with transaction() as cursor:
            cursor.execute(
                """SELECT encrypted_secret FROM ai_credentials
                   WHERE id = %s AND teacher_id = %s AND status IN ('pending','active')""",
                (credential_id, teacher_id),
            )
            row = cursor.fetchone()
            encoded = row["encrypted_secret"] if row else None
    else:
        row = store.ai_credentials.get(credential_id)
        encoded = row["encoded"] if row and row["teacherId"] == teacher_id and row["status"] != "revoked" else None
    if not encoded:
        fail(409, "SOLAR_KEY_REQUIRED", ERROR_MESSAGES["SOLAR_KEY_REQUIRED"])
    try:
        return _cipher().decrypt(encoded).decode()
    except InvalidToken:
        fail(503, "SOLAR_ENCRYPTION_REQUIRED", "Solar 키를 복호화할 수 없습니다. 암호화 설정을 확인한 뒤 키를 다시 등록해 주세요.")
