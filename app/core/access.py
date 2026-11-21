from datetime import datetime, timezone
from hashlib import sha256
from hmac import compare_digest

from fastapi import Request

from app.core.dto import Role
from app.core.errors import fail
from app.core.state import MemoryStore, Record, get_store

COOKIE_NAME = "ganada_session"


def session_digest(token: str) -> str:
    return sha256(token.encode()).hexdigest()


def session_csrf(token: str) -> str:
    # Derive a separate value without storing the original session token or exposing it to JS.
    return sha256(b"ganada:csrf:v1\0" + token.encode()).hexdigest()


def get_session(request: Request) -> Record:
    state = get_store(request)
    token = request.cookies.get(COOKIE_NAME)
    session = state.sessions.get(session_digest(token)) if token else None
    if session is None:
        fail(401, "UNAUTHENTICATED", "로그인이 필요합니다.")
    if session["expiresAt"] <= datetime.now(timezone.utc):
        del state.sessions[session_digest(token)]
        fail(401, "SESSION_INVALID", "세션이 만료되었습니다.")
    return session


def auth(request: Request, role: Role | None = None, learning: bool = False) -> Record:
    state = get_store(request)
    session = get_session(request)
    user = state.users.get(session["userId"])
    if not user or user["status"] != "active" or (user["role"] == "teacher" and not user["verified"]):
        fail(401, "SESSION_INVALID", "활성 계정이 아닙니다.")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        supplied = request.headers.get("X-CSRF-Token", "").encode()
        if not compare_digest(supplied, session["csrfToken"].encode()):
            fail(403, "CSRF_INVALID", "CSRF 토큰을 확인해 주세요.")
    if role and user["role"] != role:
        fail(403, "ROLE_FORBIDDEN", "이 역할로 사용할 수 없습니다.")
    if learning and user["role"] == "student":
        diagnostic = state.diagnostics.get(user["id"], {})
        if diagnostic.get("status") != "completed":
            fail(403, "DIAGNOSTIC_REQUIRED", "최초 진단을 완료해 주세요.")
    return user


def require_active_teacher(state: MemoryStore, user_id: str) -> Record:
    teacher = state.users.get(user_id)
    if (not teacher or teacher["role"] != "teacher"
            or teacher["status"] != "active" or not teacher["verified"]):
        fail(403, "ROLE_FORBIDDEN", "활성 상태의 승인된 교사만 초안을 등록할 수 있습니다.")
    return teacher


def find_membership(state: MemoryStore, user: Record, class_id: str) -> Record | None:
    group = state.classes.get(class_id)
    if not group or group["status"] != "active":
        return None
    return next(
        (member for member in state.memberships.values()
         if member["classId"] == class_id and member["userId"] == user["id"]
         and member["role"] == user["role"] and member["status"] == "active"),
        None,
    )


def membership(state: MemoryStore, user: Record, class_id: str, role: Role | None = None) -> Record:
    member = find_membership(state, user, class_id)
    if member is None or (role and member["role"] != role):
        fail(404, "NOT_FOUND", "클래스 또는 자료를 찾을 수 없습니다.")
    return member


def active_student_membership(state: MemoryStore, member_id: str, class_id: str) -> Record | None:
    member = state.memberships.get(member_id)
    if (not member or member["classId"] != class_id
            or member["role"] != "student" or member["status"] != "active"):
        return None
    user = state.users.get(member["userId"])
    group = state.classes.get(class_id)
    if user and user["role"] == "student" and user["status"] == "active" and group and group["status"] == "active":
        return member
    return None


def visible_resource(state: MemoryStore, user: Record, record: Record | None) -> bool:
    if record is None:
        return False
    member = find_membership(state, user, record["classId"])
    source = state.sources.get(record["sourceId"])
    if (member is None or not source or source["archived"]
            or source["classId"] != record["classId"] or source["subject"] != record["subject"]):
        return False
    if user["role"] == "teacher":
        return member["teacherSubject"] == record["subject"]
    return member["id"] == record["studentMemberId"] and record["status"] == "published"


def require_resource(state: MemoryStore, user: Record, record: Record | None, write: bool = False) -> Record:
    if not visible_resource(state, user, record):
        fail(404, "NOT_FOUND", "자료를 찾을 수 없습니다.")
    if write and (user["role"] != "teacher" or user["id"] not in {
        record["createdBy"], state.classes[record["classId"]]["ownerId"]
    }):
        fail(403, "EDIT_FORBIDDEN", "작성자 또는 클래스 소유자만 수정할 수 있습니다.")
    return record
