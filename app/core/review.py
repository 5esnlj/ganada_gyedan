from datetime import datetime, timedelta, timezone

from fastapi import Request, Response

from app.core.errors import fail
from app.core.state import Record, timestamp


def _etag(record: Record) -> str:
    return f'"{record["version"]}"'


def check_version(request: Request, record: Record) -> None:
    version = request.headers.get("If-Match")
    if version is None:
        fail(428, "VERSION_REQUIRED", "If-Match에 현재 ETag를 전달해 주세요.")
    if version != _etag(record):
        fail(412, "VERSION_CONFLICT", "다른 변경이 있습니다. 다시 조회해 주세요.")


def set_etag(response: Response, record: Record) -> None:
    response.headers["ETag"] = _etag(record)


def bump_version(record: Record) -> None:
    record["version"] += 1
    record["updatedAt"] = timestamp()


def transition(record: Record, target: str) -> None:
    previous, field = {"reviewed": ("draft", "reviewedAt"), "published": ("reviewed", "publishedAt")}[target]
    if record["status"] != previous or record.get("publishedAt"):
        fail(409, "STATE_CONFLICT", f"{previous} 상태에서만 처리할 수 있습니다.")
    record["status"] = target
    record[field] = timestamp()
    bump_version(record)


def is_recent(value: str | None) -> bool:
    if value is None:
        return False
    now = datetime.now(timezone.utc)
    return now - timedelta(days=7) <= datetime.fromisoformat(value) <= now
