"""Read a local object only after the source authorization check."""

from hashlib import sha256
import os
from pathlib import Path

from app.core.errors import fail
from app.materials.pdf import MAX_UPLOAD_BYTES

SEED_FILES = Path(__file__).resolve().parents[2] / "docs" / "mariadb-seed" / "files"
LOCAL_FILES = Path(__file__).resolve().parents[2] / ".ganada-files"


def object_root(key: str) -> Path:
    default = SEED_FILES if key.startswith("seed/") else LOCAL_FILES
    return Path(os.getenv("GANADA_DB_FILES_DIR", str(default))).resolve()


def read_source_file(file):
    root = object_root(file["objectKey"])
    path = (root / file["objectKey"]).resolve()
    if not path.is_relative_to(root) or file["size"] > MAX_UPLOAD_BYTES:
        fail(404, "NOT_FOUND", "파일을 찾을 수 없습니다.")
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_UPLOAD_BYTES + 1)
    except OSError:
        fail(404, "NOT_FOUND", "파일을 찾을 수 없습니다.")
    if len(data) != file["size"] or sha256(data).digest() != file["checksum"]:
        fail(404, "NOT_FOUND", "파일을 찾을 수 없습니다.")
    return data
