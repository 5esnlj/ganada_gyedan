from fastapi import APIRouter, Request, Response

from app.core.access import COOKIE_NAME, auth, get_session, session_digest
from app.core.state import Store
from app.identity import service
from app.identity.dto import CurrentSessionOut, Login, SessionOut

router = APIRouter(prefix="/auth", tags=["인증"])


@router.post("/login", response_model=SessionOut)
def login(body: Login, request: Request, response: Response, state: Store) -> SessionOut:
    token, payload = service.login(state, body, request.cookies.get(COOKIE_NAME))
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=int(service.SESSION_AGE.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=request.app.state.secure_cookies,
    )
    response.headers["Cache-Control"] = "no-store"
    return payload


@router.get("/me", response_model=CurrentSessionOut)
def me(request: Request, response: Response, state: Store) -> CurrentSessionOut:
    user = auth(request)
    session = get_session(request)
    response.headers["Cache-Control"] = "no-store"
    payload = service.session_response(state, user, session["csrfToken"])
    return CurrentSessionOut(**payload.model_dump())


@router.post("/logout", status_code=204)
def logout(request: Request, state: Store) -> Response:
    auth(request)
    state.sessions.pop(session_digest(request.cookies[COOKIE_NAME]), None)
    response = Response(status_code=204, headers={"Cache-Control": "no-store"})
    response.delete_cookie(
        COOKIE_NAME, httponly=True, samesite="lax", secure=request.app.state.secure_cookies
    )
    return response
