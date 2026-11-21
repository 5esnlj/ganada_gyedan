"""Persist only the SHA-256 session identifier in MariaDB."""

from datetime import timezone

from app.core.access import session_csrf, session_digest
from app.database import transaction


def load_session(cursor, store, token):
    if not token:
        return
    digest = session_digest(token)
    cursor.execute(
        """SELECT user_id, expires_at FROM user_sessions
           WHERE token_hash = %s AND revoked_at IS NULL AND expires_at > UTC_TIMESTAMP(6)""",
        (bytes.fromhex(digest),),
    )
    row = cursor.fetchone()
    if row:
        store.sessions[digest] = {
            "userId": str(row["user_id"]), "expiresAt": row["expires_at"].replace(tzinfo=timezone.utc),
            "csrfToken": session_csrf(token),
        }


def login_hash(login_id):
    with transaction() as cursor:
        cursor.execute("SELECT password_hash FROM users WHERE login_id = %s", (login_id,))
        row = cursor.fetchone()
        return row["password_hash"] if row else None


def _revoke(cursor, token):
    cursor.execute(
        "UPDATE user_sessions SET revoked_at = UTC_TIMESTAMP(6) WHERE token_hash = %s AND revoked_at IS NULL",
        (bytes.fromhex(session_digest(token)),),
    )


def save_login(token, old_token, session):
    with transaction() as cursor:
        if old_token:
            _revoke(cursor, old_token)
        cursor.execute(
            "INSERT INTO user_sessions (user_id, token_hash, expires_at) VALUES (%s, %s, %s)",
            (session["userId"], bytes.fromhex(session_digest(token)), session["expiresAt"].replace(tzinfo=None)),
        )


def revoke(token):
    with transaction() as cursor:
        _revoke(cursor, token)
