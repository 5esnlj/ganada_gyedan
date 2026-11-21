from fastapi import APIRouter, Request, Response

from app.classes import service
from app.classes.dto import (
    AcceptInvite,
    AcceptInviteOut,
    ClassCreate,
    ClassOut,
    InvitationOut,
    InviteCreate,
    MembershipOut,
)
from app.core.access import auth
from app.core.dto import Page
from app.core.state import Store
from app.identity.service import next_action

router = APIRouter(tags=["클래스"])


@router.get("/classes", response_model=Page[ClassOut])
def classes(request: Request, state: Store) -> Page[ClassOut]:
    return Page[ClassOut](items=service.list_classes(state, auth(request)))


@router.post("/classes", response_model=ClassOut, status_code=201)
def create_class(body: ClassCreate, request: Request, state: Store) -> ClassOut:
    return service.create_class(state, auth(request, "teacher"), body)


@router.post("/classes/{classId}/invitations", response_model=InvitationOut, status_code=201)
def create_invite(
    classId: str, body: InviteCreate, request: Request, response: Response, state: Store
) -> InvitationOut:
    invitation = service.create_invitation(state, auth(request, "teacher"), classId, body)
    response.headers["Cache-Control"] = "no-store"
    return invitation


@router.post("/invitations/accept", response_model=AcceptInviteOut)
def accept_invite(body: AcceptInvite, request: Request, state: Store) -> AcceptInviteOut:
    user = auth(request)
    member, already_member = service.accept_invitation(state, user, body.code)
    return AcceptInviteOut(
        membership=MembershipOut.model_validate(member),
        alreadyMember=already_member,
        nextAction=next_action(state, user),
    )
