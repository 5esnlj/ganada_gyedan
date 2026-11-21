from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.core.dto import Area, Input, Role
from app.identity.dto import NextAction


class ClassCreate(Input):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]
    gradeLabel: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)] = "중1"
    academicYear: int = Field(default=2026, ge=2000, le=2200)
    teacherSubject: Area


class ClassOut(BaseModel):
    id: str
    name: str
    gradeLabel: str
    academicYear: int
    ownerId: str
    status: Literal["active", "archived"]
    membershipId: str
    teacherSubject: Area | None


class InviteCreate(Input):
    invitedRole: Role
    teacherSubject: Area | None = None
    maxUses: int = Field(default=1, ge=1, le=100)
    expiresInHours: int = Field(default=24, ge=1, le=168)

    @model_validator(mode="after")
    def validate_subject(self) -> "InviteCreate":
        if self.invitedRole == "teacher" and self.teacherSubject is None:
            raise ValueError("교사 초대에는 소유자가 지정한 담당 교과가 필요합니다.")
        if self.invitedRole == "student" and self.teacherSubject is not None:
            raise ValueError("학생 초대에는 담당 교과를 지정할 수 없습니다.")
        return self


class InvitationOut(BaseModel):
    id: str
    classId: str
    createdBy: str
    code: str
    invitedRole: Role
    teacherSubject: Area | None
    maxUses: int
    usedCount: int
    expiresAt: datetime
    revoked: bool


class AcceptInvite(Input):
    code: str = Field(min_length=1, max_length=100)


class MembershipOut(BaseModel):
    id: str
    classId: str
    userId: str
    role: Role
    teacherSubject: Area | None
    status: Literal["active", "left", "removed"]


class AcceptInviteOut(BaseModel):
    membership: MembershipOut
    alreadyMember: bool
    nextAction: NextAction
