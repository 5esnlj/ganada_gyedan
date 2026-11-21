from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from secrets import token_urlsafe

from app.classes.dto import ClassCreate, ClassOut, InvitationOut, InviteCreate
from app.core.access import membership
from app.core.errors import fail
from app.core.state import MemoryStore, Record


def active_class_memberships(state: MemoryStore, user: Record) -> Iterator[tuple[Record, Record]]:
    for member in state.memberships.values():
        if (
            member["userId"] != user["id"]
            or member["role"] != user["role"]
            or member["status"] != "active"
        ):
            continue
        classroom = state.classes.get(member["classId"])
        if classroom and classroom["status"] == "active":
            yield classroom, member


def _class_out(classroom: Record, member: Record) -> ClassOut:
    return ClassOut.model_validate({
        **classroom,
        "teacherSubject": member["teacherSubject"],
        "membershipId": member["id"],
    })


def list_classes(state: MemoryStore, user: Record) -> list[ClassOut]:
    active = {
        classroom["id"]: member
        for classroom, member in active_class_memberships(state, user)
    }
    return [
        _class_out(classroom, active[class_id])
        for class_id, classroom in reversed(state.classes.items())
        if class_id in active
    ]


def create_class(state: MemoryStore, user: Record, body: ClassCreate) -> ClassOut:
    class_id = state.new_id("cls")
    member_id = state.new_id("mem")
    classroom = {
        **body.model_dump(exclude={"teacherSubject"}),
        "id": class_id,
        "ownerId": user["id"],
        "status": "active",
    }
    member = {
        "id": member_id,
        "classId": class_id,
        "userId": user["id"],
        "role": "teacher",
        "teacherSubject": body.teacherSubject,
        "status": "active",
    }
    payload = _class_out(classroom, member)
    state.classes[class_id] = classroom
    state.memberships[member_id] = member
    return payload


def invitation_digest(code: str) -> str:
    return sha256(code.encode()).hexdigest()


def create_invitation(
    state: MemoryStore, user: Record, class_id: str, body: InviteCreate
) -> InvitationOut:
    membership(state, user, class_id, "teacher")
    if body.invitedRole == "teacher" and state.classes[class_id]["ownerId"] != user["id"]:
        fail(403, "OWNER_REQUIRED", "교사 초대는 클래스 소유자만 발급합니다.")
    code = token_urlsafe(24)
    invitation = {
        "id": state.new_id("inv"),
        "classId": class_id,
        "createdBy": user["id"],
        "invitedRole": body.invitedRole,
        "teacherSubject": body.teacherSubject,
        "maxUses": body.maxUses,
        "usedCount": 0,
        "expiresAt": datetime.now(timezone.utc) + timedelta(hours=body.expiresInHours),
        "revoked": False,
    }
    payload = InvitationOut(code=code, **invitation)
    state.invitations[invitation_digest(code)] = invitation
    return payload


def accept_invitation(state: MemoryStore, user: Record, code: str) -> tuple[Record, bool]:
    invitation = state.invitations.get(invitation_digest(code))
    if (
        not invitation
        or invitation["revoked"]
        or invitation["expiresAt"] <= datetime.now(timezone.utc)
        or state.classes.get(invitation["classId"], {}).get("status") != "active"
    ):
        fail(404, "INVITATION_INVALID", "유효한 초대가 아닙니다.")
    if user["role"] != invitation["invitedRole"]:
        fail(403, "INVITATION_ROLE_MISMATCH", "초대의 역할이 계정 역할과 다릅니다.")

    class_id = invitation["classId"]
    existing = next(
        (
            member for member in state.memberships.values()
            if member["classId"] == class_id and member["userId"] == user["id"]
        ),
        None,
    )
    if existing:
        if existing["status"] != "active":
            fail(409, "MEMBERSHIP_REAPPROVAL_REQUIRED", "소유자의 재가입 승인이 필요합니다.")
        return existing, True
    if invitation["usedCount"] >= invitation["maxUses"]:
        fail(409, "INVITATION_EXHAUSTED", "초대 사용 횟수가 소진되었습니다.")

    member_id = state.new_id("mem")
    member = {
        "id": member_id,
        "classId": class_id,
        "userId": user["id"],
        "role": user["role"],
        "teacherSubject": invitation["teacherSubject"],
        "status": "active",
        "invitationId": invitation["id"],
    }
    state.memberships[member_id] = member
    invitation["usedCount"] += 1
    return member, False
