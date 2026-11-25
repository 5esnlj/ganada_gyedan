"""Prepare, run and commit each student PDF job without holding the HTTP lock during AI calls."""

from functools import partial
import logging

from starlette.concurrency import run_in_threadpool

from app.core.access import active_student_membership, require_active_teacher
from app.core.errors import APIError, fail
from app.diagnostic.dto import DiagnosticResults
from app.diagnostic.service import completed_results
from app.mariadb.store import load_store
from app.materials.service import create_draft, require_source

from . import credentials, jobs, solar
from .dto import PdfPage
from .prompts import convert_pdf

logger = logging.getLogger(__name__)


def _context(store, job):
    teacher = require_active_teacher(store, job["createdBy"])
    source = require_source(store, teacher, job["sourceId"])
    member = active_student_membership(store, job["studentMemberId"], job["classId"])
    if source["classId"] != job["classId"] or member is None or completed_results(store, member["userId"]) is None:
        fail(409, "SOURCE_CHANGED", solar.ERROR_MESSAGES["SOURCE_CHANGED"])
    return teacher, source


def _prepare(store, job, database):
    current = load_store(None, include_learning=True) if database else store
    _, source = _context(current, job)
    key = credentials.unlock(current, job["createdBy"], job["credentialId"])
    pages = [PdfPage.model_validate(page) for page in job["snapshot"]["pages"]]
    return key, source, pages


def _commit(store, job, result, database):
    current = load_store(None, include_learning=True) if database else store
    teacher, _ = _context(current, job)
    draft = create_draft(current, teacher, job["sourceId"], job["studentMemberId"], result)
    draft["diagnosisSnapshot"] = job["snapshot"]["diagnosis"]
    jobs.succeed(current, job, draft, database)


async def run_jobs(app, job_ids):
    database = getattr(app.state, "database_connected", False)
    store = app.state.store

    async def storage(operation, *args):
        if database:
            return await run_in_threadpool(operation, *args)
        async with app.state.request_lock:
            return operation(*args)

    for job_id in job_ids:
        # Process one student at a time on this server, while serving other UI requests.
        async with app.state.generation_slot:
            try:
                job = await storage(jobs.claim, store, job_id, database)
            except APIError:
                logger.warning("Could not claim PDF job %s", job_id)
                continue
            if not job:
                continue
            page = None
            try:
                key, source, pages = await storage(_prepare, store, job, database)
                call = partial(solar.complete_json, key, job["model"], for_pdf=True)
                snapshot = job["snapshot"]["diagnosis"]
                diagnosis = DiagnosticResults.model_validate(snapshot)
                result = await run_in_threadpool(convert_pdf, call, source, diagnosis, snapshot.get("nativeLanguage", ""), pages)
                await storage(_commit, store, job, result, database)
                continue
            except APIError as error:
                code = error.code if error.code in solar.ERROR_MESSAGES else "SOURCE_CHANGED"
                if isinstance(error.details, dict):
                    page = error.details.get("page")
                logger.warning("PDF job %s failed (%s, page=%s)", job_id, code, page)
            except Exception as error:
                logger.error("PDF job %s failed (%s)", job_id, type(error).__name__)
                code = "GENERATION_FAILED"
            try:
                await storage(jobs.failed, store, job_id, code, database, page)
            except APIError:
                logger.warning("Could not record the outcome of PDF job %s", job_id)
