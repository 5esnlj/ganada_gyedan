from typing import Any
from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException


class ErrorOut(BaseModel):
    code: str
    message: str
    requestId: str
    details: Any = None


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def fail(status: int, code: str, message: str, details: Any = None) -> None:
    raise APIError(status, code, message, details)


def response(status, code, message, details=None, headers=None):
    body = ErrorOut(code=code, message=message, requestId=uuid4().hex, details=details)
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"), headers=headers)


async def api_error(request: Request, exc: APIError):
    return response(exc.status, exc.code, exc.message, exc.details)


async def validation_error(request: Request, exc: RequestValidationError):
    details = [
        {"field": ".".join(map(str, item["loc"])), "message": item["msg"], "type": item["type"]}
        for item in exc.errors()
    ]
    return response(422, "VALIDATION_ERROR", "입력값을 확인해 주세요.", details)


async def http_error(request: Request, exc: HTTPException):
    codes = {400: "BAD_REQUEST", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}
    return response(exc.status_code, codes.get(exc.status_code, "HTTP_ERROR"), str(exc.detail), headers=exc.headers)


ERROR_RESPONSES = {
    400: {"model": ErrorOut, "description": "잘못된 요청"},
    401: {"model": ErrorOut, "description": "로그인 필요"},
    403: {"model": ErrorOut, "description": "역할, CSRF 또는 진단 상태 제한"},
    404: {"model": ErrorOut, "description": "리소스 없음 또는 접근 불가"},
    409: {"model": ErrorOut, "description": "상태 또는 중복 요청 충돌"},
    412: {"model": ErrorOut, "description": "오래된 버전"},
    413: {"model": ErrorOut, "description": "업로드 크기 초과"},
    415: {"model": ErrorOut, "description": "지원하지 않는 파일 형식"},
    422: {"model": ErrorOut, "description": "입력 검증 실패"},
    428: {"model": ErrorOut, "description": "If-Match 필요"},
}
