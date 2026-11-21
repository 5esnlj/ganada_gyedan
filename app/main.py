import asyncio

from fastapi import APIRouter, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.exceptions import HTTPException

from app.classes.router import router as classes_router
from app.core.errors import APIError, ERROR_RESPONSES, api_error, http_error, validation_error
from app.core.openapi import add_request_headers
from app.core.state import MemoryStore
from app.dashboard.router import router as dashboard_router
from app.diagnostic.fixtures import demo_provider, seed_diagnostics
from app.diagnostic.forms import FormProvider, StaticFormProvider
from app.diagnostic.router import router as diagnostic_router
from app.identity.router import router as identity_router
from app.materials.router import router as materials_router
from app.quizzes.router import router as quizzes_router
from app.seed import seed_catalog


class Health(BaseModel):
    status: str = "ok"
    mode: str = "memory"
    databaseConnected: bool = False
    solarConnected: bool = False
    isSynthetic: bool


def create_app(
    *,
    store: MemoryStore | None = None,
    diagnostic_form_provider: FormProvider | None = None,
    secure_cookies: bool = False,
) -> FastAPI:
    demo = store is None
    state = MemoryStore() if demo else store

    application = FastAPI(
        title="가나다계단 API",
        version="0.1.0",
        description="가나다계단 로컬 개발 API. 기본 실행에는 가상 계정과 자료가 포함됩니다.",
    )
    application.state.store = state
    application.state.secure_cookies = secure_cookies
    if demo:
        seed_catalog(state)
    if diagnostic_form_provider is None:
        diagnostic_form_provider = demo_provider() if demo else StaticFormProvider(None)
        if demo:
            seed_diagnostics(state, diagnostic_form_provider)
    application.state.diagnostic_form_provider = diagnostic_form_provider

    application.add_exception_handler(APIError, api_error)
    application.add_exception_handler(RequestValidationError, validation_error)
    application.add_exception_handler(HTTPException, http_error)

    request_lock = asyncio.Lock()

    @application.middleware("http")
    async def serialize_api(request, call_next):
        if not request.url.path.startswith("/api/v1/"):
            return await call_next(request)
        async with request_lock:
            response = await call_next(request)
        # All learning data is private. Download and JSON responses follow the same policy.
        response.headers["Cache-Control"] = "no-store"
        return response

    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "If-Match", "Idempotency-Key"],
        expose_headers=["ETag"],
    )

    api = APIRouter(prefix="/api/v1", responses=ERROR_RESPONSES)

    @api.get("/health", response_model=Health, tags=["공통"])
    def health():
        return Health(isSynthetic=demo)

    for router in (identity_router, classes_router, diagnostic_router, materials_router, quizzes_router, dashboard_router):
        api.include_router(router)
    application.include_router(api)
    add_request_headers(application)
    return application


app = create_app()
