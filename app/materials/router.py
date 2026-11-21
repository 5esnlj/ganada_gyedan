"""HTTP endpoints for the source and converted material workflow."""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Request, Response, UploadFile

from app.core.access import auth
from app.core.dto import Area, Page
from app.core.review import check_version, set_etag
from app.core.state import Store
from app.materials import service
from app.materials.dto import MaterialOut, MaterialPatch, SourceCreate, SourceOut, UploadOut
from app.materials.pdf import MAX_UPLOAD_BYTES

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
        file["data"],
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


@router.get("/classes/{classId}/sources", response_model=Page[SourceOut], tags=["원본 자료"])
def sources(classId: str, request: Request, store: Store, subject: Area | None = None):
    teacher = auth(request, "teacher")
    return {"items": service.list_sources(store, teacher, classId, subject)}


@router.get("/sources/{sourceId}", response_model=SourceOut, tags=["원본 자료"])
def source(sourceId: str, request: Request, store: Store):
    teacher = auth(request, "teacher")
    return service.require_source(store, teacher, sourceId)


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


@router.patch("/materials/{materialId}", response_model=MaterialOut, tags=["전환 자료"])
def patch_material(
    materialId: str, body: MaterialPatch, request: Request, response: Response, store: Store
):
    teacher = auth(request, "teacher")
    record = service.require_material(store, teacher, materialId, write=True)
    check_version(request, record)
    service.update_content(record, body.content)
    set_etag(response, record)
    return service.material_view(store, teacher, record)


def _transition(
    material_id: str,
    target: str,
    request: Request,
    response: Response,
    store: Store,
) -> MaterialOut:
    teacher = auth(request, "teacher")
    record = service.require_material(store, teacher, material_id, write=True)
    check_version(request, record)
    service.transition_material(store, teacher, record, target)
    set_etag(response, record)
    return service.material_view(store, teacher, record)


@router.post("/materials/{materialId}/review", response_model=MaterialOut, tags=["전환 자료"])
def review_material(materialId: str, request: Request, response: Response, store: Store):
    return _transition(materialId, "reviewed", request, response, store)


@router.post("/materials/{materialId}/publish", response_model=MaterialOut, tags=["전환 자료"])
def publish_material(materialId: str, request: Request, response: Response, store: Store):
    return _transition(materialId, "published", request, response, store)
