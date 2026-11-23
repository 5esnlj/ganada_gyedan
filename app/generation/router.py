"""Teacher entry points for PDF reconstruction jobs and their Solar key."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Header, Request

from app.core.access import active_student_membership, auth, membership
from app.core.dto import Page
from app.core.errors import fail
from app.core.state import Store
from app.diagnostic.service import completed_results
from app.mariadb.files import read_source_file
from app.materials.pdf import extract_pdf_pages
from app.materials.service import require_file, require_source

from . import credentials, jobs, solar
from .dto import CredentialCreate, CredentialOut, Generate, JobOut
from .worker import run_jobs

router = APIRouter(tags=["PDF 자료 제작"])


@router.get("/ai-credentials", response_model=Page[CredentialOut])
def saved_credentials(request: Request, store: Store):
    teacher = auth(request, "teacher")
    return {"items": credentials.rows(store, teacher["id"])}


@router.post("/ai-credentials", status_code=201, response_model=CredentialOut)
def register_credential(body: CredentialCreate, request: Request, store: Store):
    teacher = auth(request, "teacher")
    return credentials.register(store, teacher["id"], body)


@router.post("/classes/{classId}/generation-jobs", status_code=202, response_model=Page[JobOut])
def start_jobs(
    classId: str, body: Generate, request: Request, store: Store, background_tasks: BackgroundTasks,
    request_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    teacher = auth(request, "teacher")
    batch_key, digest = jobs.request_keys(body, request_key)
    source = require_source(store, teacher, body.sourceId)
    if source["classId"] != classId or not source["fileId"]:
        fail(404, "NOT_FOUND", "담당 클래스의 원본 PDF를 선택해 주세요.")
    file = require_file(store, teacher, source["fileId"])
    data = read_source_file(file) if "objectKey" in file else file["data"]
    if body.pages[-1].number > len(extract_pdf_pages(data)):
        fail(422, "PDF_PAGE_RANGE", solar.ERROR_MESSAGES["PDF_PAGE_RANGE"])

    keys = credentials.rows(store, teacher["id"])
    if not keys:
        fail(409, "SOLAR_KEY_REQUIRED", solar.ERROR_MESSAGES["SOLAR_KEY_REQUIRED"])

    candidates = []
    model = solar.model_name()
    pages = [p.model_dump(mode="json") for p in body.pages]
    for student_id in body.studentMemberIds:
        member = active_student_membership(store, student_id, classId)
        if not member:
            fail(404, "NOT_FOUND", "이 클래스의 학생을 선택해 주세요.")
        student = store.users[member["userId"]]
        result = completed_results(store, student["id"])
        if result is None:
            fail(409, "STUDENT_DIAGNOSTIC_REQUIRED", "진단평가를 완료한 학생을 선택해 주세요.")
        reading = next((p for p in result.parts if p.area == source["subject"] and p.skill == "reading"), None)
        if reading is None:
            fail(409, "STUDENT_DIAGNOSTIC_REQUIRED", "담당 교과의 읽기 수준이 없습니다.")
        candidates.append({
            "classId": classId, "sourceId": body.sourceId, "studentMemberId": student_id,
            "credentialId": keys[0]["id"], "createdBy": teacher["id"], "status": "queued", "model": model,
            "snapshot": {
                "batchKey": batch_key, "digest": digest, "pages": pages,
                "learner": {"level": reading.level, "nativeLanguage": student["nativeLanguage"]},
                "diagnosis": result.model_dump(mode="json") | {"nativeLanguage": student["nativeLanguage"]},
            },
        })
    batch, created = jobs.batch(store, teacher["id"], classId, candidates)
    if created:
        background_tasks.add_task(run_jobs, request.app, [job["id"] for job in batch])
    return {"items": [jobs.public(job) for job in batch]}


@router.get("/classes/{classId}/generation-jobs", response_model=Page[JobOut])
def list_jobs(classId: str, request: Request, store: Store):
    teacher = auth(request, "teacher")
    membership(store, teacher, classId, "teacher")
    return {"items": [jobs.public(job) for job in jobs.list_owned(store, teacher["id"], classId)]}


@router.get("/generation-jobs/{jobId}", response_model=JobOut)
def job_detail(jobId: str, request: Request, store: Store):
    teacher = auth(request, "teacher")
    job = jobs.get(store, jobId)
    if not job or job["createdBy"] != teacher["id"]:
        fail(404, "NOT_FOUND", "생성 작업을 찾을 수 없습니다.")
    membership(store, teacher, job["classId"], "teacher")
    return jobs.public(job)
