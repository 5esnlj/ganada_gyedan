"""Source registration, authorization and student material lifecycle."""

from hashlib import sha256
from pathlib import PurePosixPath
from typing import get_args

from app.core.access import (
    active_student_membership,
    membership,
    require_active_teacher,
    require_resource,
    visible_resource,
)
from app.core.dto import Area
from app.core.errors import fail
from app.core.review import bump_version, is_recent, transition
from app.core.state import MemoryStore, new_id, timestamp
from app.diagnostic.service import completed_results
from app.materials.dto import Content, MaterialOut, SourceCreate, UploadOut
from app.materials.pdf import MAX_UPLOAD_BYTES, extract_source_text

SUBJECT_ORDER = get_args(Area)


def store_upload(
    state: MemoryStore,
    teacher: dict,
    class_id: str,
    name: str | None,
    media_type: str | None,
    data: bytes,
) -> UploadOut:
    member = membership(state, teacher, class_id, "teacher")
    if len(data) > MAX_UPLOAD_BYTES:
        fail(413, "FILE_TOO_LARGE", "PDF는 최대 10 MiB입니다.")
    if media_type != "application/pdf" or not data.startswith(b"%PDF-"):
        fail(415, "PDF_REQUIRED", "PDF 파일을 올려 주세요.")

    source_text, sentences = extract_source_text(data)
    basename = PurePosixPath((name or "").replace("\\", "/")).name
    filename = "".join(char for char in basename if char.isprintable()).strip(" .")
    upload = UploadOut(
        id=new_id("file"),
        classId=class_id,
        createdBy=teacher["id"],
        subject=member["teacherSubject"],
        name=filename[:255] or "upload.pdf",
        size=len(data),
        extractionStatus="ready",
        notice="PDF 본문을 추출했습니다.",
    )
    state.files[upload.id] = {
        **upload.model_dump(),
        "data": data,
        "checksum": sha256(data).hexdigest(),
        "sourceText": source_text,
        "sentences": sentences,
    }
    return upload


def create_source(
    state: MemoryStore, teacher: dict, class_id: str, body: SourceCreate
) -> dict:
    member = membership(state, teacher, class_id, "teacher")
    file = state.files.get(body.fileId)
    if (
        not file
        or file["classId"] != class_id
        or file["createdBy"] != teacher["id"]
        or file["subject"] != member["teacherSubject"]
        or not file.get("sourceText")
    ):
        fail(404, "NOT_FOUND", "업로드 파일을 찾을 수 없습니다.")

    source = {
        "id": new_id("src"),
        "classId": class_id,
        "createdBy": teacher["id"],
        "fileId": body.fileId,
        "title": body.title,
        "subject": member["teacherSubject"],
        "unit": body.unit,
        "sourceText": file["sourceText"],
        "sentences": file["sentences"],
        "createdAt": timestamp(),
        "archived": False,
    }
    state.sources[source["id"]] = source
    return source


def require_source(state: MemoryStore, teacher: dict, source_id: str) -> dict:
    source = state.sources.get(source_id)
    if not source or source.get("archived"):
        fail(404, "NOT_FOUND", "원본 자료를 찾을 수 없습니다.")
    member = membership(state, teacher, source["classId"], "teacher")
    if member["teacherSubject"] != source["subject"]:
        fail(404, "NOT_FOUND", "원본 자료를 찾을 수 없습니다.")
    return source


def list_sources(
    state: MemoryStore, teacher: dict, class_id: str, subject: Area | None = None
) -> list[dict]:
    member = membership(state, teacher, class_id, "teacher")
    own_subject = member["teacherSubject"]
    if subject is not None and subject != own_subject:
        return []
    return sorted(
        (
            source
            for source in state.sources.values()
            if source["classId"] == class_id
            and source["subject"] == own_subject
            and not source.get("archived")
        ),
        key=lambda source: (source["createdAt"], source["id"]),
        reverse=True,
    )


def require_file(state: MemoryStore, user: dict, file_id: str) -> dict:
    file = state.files.get(file_id)
    if not file or not (file.get("data") or file.get("objectKey")):
        fail(404, "NOT_FOUND", "파일을 찾을 수 없습니다.")
    class_id = file["classId"]
    member = membership(state, user, class_id)
    source_ids = {
        source["id"]
        for source in state.sources.values()
        if source.get("fileId") == file_id
        and source["classId"] == class_id
        and source["subject"] == file["subject"]
        and not source.get("archived")
    }
    if user["role"] == "teacher":
        allowed = bool(source_ids) and member["teacherSubject"] == file["subject"]
    else:
        allowed = any(
            quiz["sourceId"] in source_ids
            and quiz["classId"] == class_id
            and not quiz.get("inputMaterialId")
            and quiz.get("approvedSentenceIds") is None
            and visible_resource(state, user, quiz)
            for quiz in state.quizzes.values()
        )
    if not allowed:
        fail(404, "NOT_FOUND", "파일을 찾을 수 없습니다.")
    return file


def material_view(state: MemoryStore, user: dict, material: dict) -> MaterialOut:
    source = state.sources[material["sourceId"]]
    return MaterialOut.model_validate(
        {
            **material,
            "unit": source["unit"],
            "isRecentPublished": is_recent(material["publishedAt"]),
            "hasPdf": bool(material.get("convertedFile")),
            "originalContent": source["sourceText"] if user["role"] == "teacher" else None,
        }
    )


def list_materials(
    state: MemoryStore, user: dict, class_id: str, subject: Area | None = None
) -> list[MaterialOut]:
    membership(state, user, class_id)
    records = [
        material
        for material in state.materials.values()
        if material["classId"] == class_id
        and (subject is None or material["subject"] == subject)
        and visible_resource(state, user, material)
    ]
    if user["role"] == "teacher":
        records.sort(key=lambda material: (material["createdAt"], material["id"]), reverse=True)
    else:
        records.sort(key=lambda material: (material["publishedAt"], material["id"]), reverse=True)
        records.sort(key=lambda material: SUBJECT_ORDER.index(material["subject"]))
    return [material_view(state, user, material) for material in records]


def require_material(
    state: MemoryStore, user: dict, material_id: str, write: bool = False
) -> dict:
    return require_resource(state, user, state.materials.get(material_id), write=write)


def create_draft(
    state: MemoryStore,
    teacher: dict,
    source_id: str,
    student_member_id: str,
    content: Content,
) -> dict:
    """Store validated content supplied by a future material producer."""
    teacher = require_active_teacher(state, teacher["id"])
    source = require_source(state, teacher, source_id)
    class_id = source["classId"]
    student = active_student_membership(state, student_member_id, class_id)
    if not student:
        fail(404, "NOT_FOUND", "대상 학생을 찾을 수 없습니다.")
    results = completed_results(state, student["userId"])
    if results is None:
        fail(
            409,
            "STUDENT_DIAGNOSTIC_REQUIRED",
            "대상 학생의 진단이 미완료입니다.",
            {"studentMemberId": student_member_id},
        )
    revision = 1 + max(
        (
            material["revision"]
            for material in state.materials.values()
            if material["sourceId"] == source_id
            and material["studentMemberId"] == student_member_id
        ),
        default=0,
    )
    material = {
        "id": new_id("mat"),
        "classId": class_id,
        "sourceId": source_id,
        "studentMemberId": student_member_id,
        "subject": source["subject"],
        "title": source["title"],
        "status": "draft",
        "version": 1,
        "revision": revision,
        "createdBy": teacher["id"],
        "content": content.model_dump(),
        "diagnosisSnapshot": {
            "nativeLanguage": state.users[student["userId"]]["nativeLanguage"],
            **results.model_dump(mode="json"),
        },
        "createdAt": timestamp(),
        "publishedAt": None,
        "reviewedAt": None,
    }
    state.materials[material["id"]] = material
    return material


def update_content(material: dict, content: Content) -> None:
    if material["status"] not in {"draft", "reviewed"} or material["publishedAt"]:
        fail(409, "PUBLISHED_IMMUTABLE", "배포된 자료는 새 버전으로 만드세요.")
    material.update(
        content=content.model_dump(), status="draft", reviewedAt=None, reviewedBy=None
    )
    bump_version(material)


def transition_material(
    state: MemoryStore, teacher: dict, material: dict, target: str
) -> None:
    if not active_student_membership(state, material["studentMemberId"], material["classId"]):
        fail(404, "NOT_FOUND", "대상 학생을 찾을 수 없습니다.")
    transition(material, target)
    material["reviewedBy" if target == "reviewed" else "publishedBy"] = teacher["id"]
