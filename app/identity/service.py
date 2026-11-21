from datetime import datetime, timedelta, timezone
from secrets import token_urlsafe

from app.classes.service import active_class_memberships
from app.core.access import session_digest
from app.core.errors import fail
from app.core.state import MemoryStore, Record
from app.identity.dto import Login, NextAction, PublicUser, SessionOut
from app.identity.passwords import demo_password_hash, verify_password

SESSION_AGE = timedelta(hours=8)


def next_action(state: MemoryStore, user: Record) -> NextAction:
    if user["role"] == "teacher":
        return "classes"

    if next(active_class_memberships(state, user), None) is None:
        return "join_class"
    if state.diagnostics.get(user["id"], {}).get("status") != "completed":
        return "diagnostic"
    return "classes"


def session_response(state: MemoryStore, user: Record, csrf_token: str) -> SessionOut:
    return SessionOut(
        user=PublicUser.model_validate(user),
        csrfToken=csrf_token,
        nextAction=next_action(state, user),
    )


def login(state: MemoryStore, body: Login, old_token: str | None) -> tuple[str, SessionOut]:
    user = next((user for user in state.users.values() if user["loginId"] == body.loginId), None)
    encoded = user.get("passwordHash") if user else None
    valid_password = verify_password(
        body.password.get_secret_value(), encoded or demo_password_hash()
    )
    if (
        not user
        or not encoded
        or not valid_password
        or user["role"] != body.role
        or user["status"] != "active"
        or (user["role"] == "teacher" and not user["verified"])
    ):
        fail(401, "INVALID_CREDENTIALS", "로그인 정보 또는 역할을 확인해 주세요.")

    now = datetime.now(timezone.utc)
    token = token_urlsafe(32)
    csrf_token = token_urlsafe(32)
    payload = session_response(state, user, csrf_token)
    if old_token:
        state.sessions.pop(session_digest(old_token), None)
    state.sessions[session_digest(token)] = {
        "userId": user["id"],
        "csrfToken": csrf_token,
        "expiresAt": now + SESSION_AGE,
    }
    return token, payload
