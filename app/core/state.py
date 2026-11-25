from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Annotated, Any
from uuid import uuid4

from fastapi import Depends, Request

Record = dict[str, Any]


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


@dataclass
class MemoryStore:
    """Per-app data for single-process development and tests."""

    users: dict[str, Record] = field(default_factory=dict)
    classes: dict[str, Record] = field(default_factory=dict)
    memberships: dict[str, Record] = field(default_factory=dict)
    sessions: dict[str, Record] = field(default_factory=dict)
    invitations: dict[str, Record] = field(default_factory=dict)
    diagnostics: dict[str, Record] = field(default_factory=dict)
    files: dict[str, Record] = field(default_factory=dict)
    sources: dict[str, Record] = field(default_factory=dict)
    materials: dict[str, Record] = field(default_factory=dict)
    quizzes: dict[str, Record] = field(default_factory=dict)
    attempts: dict[str, dict[str, list[Record]]] = field(default_factory=dict)
    idempotency: dict[tuple[str, ...], Record] = field(default_factory=dict)
    ai_credentials: dict[str, Record] = field(default_factory=dict)
    generation_jobs: dict[str, Record] = field(default_factory=dict)
    quiz_jobs: dict[str, Record] = field(default_factory=dict)

    @staticmethod
    def new_id(prefix: str) -> str:
        return new_id(prefix)


def get_store(request: Request) -> MemoryStore:
    if getattr(request.app.state, "database_connected", False):
        if not hasattr(request.state, "database_store"):
            from app.core.access import COOKIE_NAME
            from app.mariadb.store import load_store

            request.state.database_store = load_store(request.cookies.get(COOKIE_NAME))
        return request.state.database_store
    return request.app.state.store


Store = Annotated[MemoryStore, Depends(get_store)]
