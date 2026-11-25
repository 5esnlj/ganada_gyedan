"""HTTP endpoints for the source and converted material workflow."""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Request, Response, UploadFile

from app.core.access import auth
from app.core.dto import Area, Page
from app.core.errors import fail
from app.core.review import check_version, set_etag
from app.core.state import Store
from app.materials import service
from app.materials.dto import MaterialOut, MaterialPatch, PdfInfo, SourceCreate, SourceOut, UploadOut
from app.materials.pdf import MAX_CONVERTED_PDF_BYTES, MAX_UPLOAD_BYTES, extract_pdf_pages, validate_converted_pdf
from app.mariadb.authoring import save_revision, save_source
from app.mariadb.files import read_source_file
from app.mariadb.store import MariaDBStore

router = APIRouter()


@router.post(
    "/classes/{classId}/files", status_code=201, response_model=UploadOut, tags=["파일"]
)
def upload(
    classId: str, request: Request, store: Store, file: Annotated[UploadFile, File()]
):
    teacher = auth(request, "teacher")
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    return service.store_upload(store, teacher, classId, file.filename, file.content_type, data)


@router.get(
    "/files/{fileId}/download",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "권한 확인 후 PDF 다운로드"}},
    tags=["파일"],
)
def download(fileId: str, request: Request, store: Store):
    user = auth(request, learning=True)
    file = service.require_file(store, user, fileId)
    filename = quote(file["name"], safe="")
    return Response(
        read_source_file(file) if "objectKey" in file else file["data"],
        media_type=file["mimeType"],
        headers={
            "Content-Disposition": f"attachment; filename=\"document.pdf\"; filename*=UTF-8''{filename}",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/classes/{classId}/sources",
    status_code=201,
    response_model=SourceOut,
    tags=["원본 자료"],
)
def register_source(classId: str, body: SourceCreate, request: Request, store: Store):
    teacher = auth(request, "teacher")
    return service.create_source(store, teacher, classId, body)


@router.post("/classes/{classId}/sources/pdf", status_code=201, response_model=SourceOut, tags=["원본 자료"])
def register_pdf(
    classId: str, request: Request, store: Store, file: Annotated[UploadFile, File()],
    title: Annotated[str, Form(min_length=1, max_length=200, pattern=r"\S")],
    unit: Annotated[str, Form(max_length=150)] = "",
):
    teacher = auth(request, "teacher")
    upload = service.store_upload(
        store, teacher, classId, file.filename, file.content_type, file.file.read(MAX_UPLOAD_BYTES + 1),
    )
    try:
        source = service.create_source(store, teacher, classId, SourceCreate(fileId=upload.id, title=title, unit=unit))
    except Exception:
        store.files.pop(upload.id, None)
        raise
    return save_source(source, store.files[upload.id]) if isinstance(store, MariaDBStore) else source


@router.get("/classes/{classId}/sources", response_model=Page[SourceOut], tags=["원본 자료"])
def sources(classId: str, request: Request, store: Store, subject: Area | None = None):
    teacher = auth(request, "teacher")
    return {"items": service.list_sources(store, teacher, classId, subject)}


@router.get("/sources/{sourceId}", response_model=SourceOut, tags=["원본 자료"])
def source(sourceId: str, request: Request, store: Store):
    teacher = auth(request, "teacher")
    return service.require_source(store, teacher, sourceId)


@router.get("/sources/{sourceId}/pdf-info", response_model=PdfInfo, tags=["원본 자료"])
def pdf_info(sourceId: str, request: Request, store: Store):
    teacher = auth(request, "teacher")
    original = service.require_source(store, teacher, sourceId)
    if not original["fileId"]:
        fail(404, "NOT_FOUND", "원본 PDF가 없습니다.")
    file = service.require_file(store, teacher, original["fileId"])
    return PdfInfo(pageCount=len(extract_pdf_pages(read_source_file(file) if "objectKey" in file else file["data"])))


@router.get(
    "/classes/{classId}/materials", response_model=Page[MaterialOut], tags=["전환 자료"]
)
def materials(classId: str, request: Request, store: Store, subject: Area | None = None):
    user = auth(request, learning=True)
    return {"items": service.list_materials(store, user, classId, subject)}


@router.get("/materials/{materialId}", response_model=MaterialOut, tags=["전환 자료"])
def material(materialId: str, request: Request, response: Response, store: Store):
    user = auth(request, learning=True)
    record = service.require_material(store, user, materialId)
    set_etag(response, record)
    return service.material_view(store, user, record)


@router.get(
    "/materials/{materialId}/download",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "배포된 전환 PDF 다운로드"}},
    tags=["전환 자료"],
)
def download_material(materialId: str, request: Request, store: Store):
    user = auth(request, learning=True)
    record = service.require_material(store, user, materialId)
    file = record.get("convertedFile")
    if record["status"] != "published" or not file:
        fail(404, "NOT_FOUND", "배포된 PDF가 없습니다.")
    filename = quote(f"{record['title']}-학습자료.pdf", safe="")
    return Response(
        read_source_file(file, MAX_CONVERTED_PDF_BYTES) if "objectKey" in file else file["data"],
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=\"material.pdf\"; filename*=UTF-8''{filename}",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.patch("/materials/{materialId}", response_model=MaterialOut, tags=["전환 자료"])
def patch_material(
    materialId: str, body: MaterialPatch, request: Request, response: Response, store: Store
):
    teacher = auth(request, "teacher")
    record = service.require_material(store, teacher, materialId, write=True)
    check_version(request, record)
    expected = record["version"]
    service.update_content(record, body.content)
    if isinstance(store, MariaDBStore):
        save_revision(record, expected)
    set_etag(response, record)
    return service.material_view(store, teacher, record)


def _transition(
    material_id: str,
    target: str,
    request: Request,
    response: Response,
    store: Store,
    file: UploadFile | None = None,
) -> MaterialOut:
    teacher = auth(request, "teacher")
    record = service.require_material(store, teacher, material_id, write=True)
    check_version(request, record)
    expected = record["version"]
    pdf = None
    if target == "published" and file is None and record["content"].get("hiddenBlockIds"):
        fail(409, "REDACTED_PDF_REQUIRED", "교사용 문단을 가린 전환 PDF를 확인한 뒤 함께 배포해 주세요.")
    if file is not None:
        pdf = file.file.read(MAX_CONVERTED_PDF_BYTES + 1)
        validate_converted_pdf(pdf)
    service.transition_material(store, teacher, record, target)
    if isinstance(store, MariaDBStore):
        save_revision(record, expected, pdf)
    elif pdf is not None:
        record["convertedFile"] = {"data": pdf}
    set_etag(response, record)
    return service.material_view(store, teacher, record)


@router.post("/materials/{materialId}/review", response_model=MaterialOut, tags=["전환 자료"])
def review_material(materialId: str, request: Request, response: Response, store: Store):
    return _transition(materialId, "reviewed", request, response, store)


@router.post("/materials/{materialId}/publish", response_model=MaterialOut, tags=["전환 자료"])
def publish_material(
    materialId: str, request: Request, response: Response, store: Store,
    file: Annotated[UploadFile | None, File()] = None,
):
    return _transition(materialId, "published", request, response, store, file)
