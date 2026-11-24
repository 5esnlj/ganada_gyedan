"""학생별 확인 문제 작업을 한 명씩 실행한다. Solar 호출 중에는 요청 잠금을 잡지 않는다."""

from functools import partial
import logging

from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from app.core.access import active_student_membership, require_active_teacher
from app.core.errors import APIError, fail
from app.diagnostic.dto import DiagnosticResults
from app.diagnostic.service import completed_results
from app.generation import credentials, solar
from app.mariadb.store import load_store
from app.materials.service import require_source

from . import jobs
from .dto import QuizDraft
from .generation import PROMPT_VERSION, diagnostic_tier, generate_items, last_history, make_plan, passage_lines
from .service import _passage, create_quiz_draft

logger = logging.getLogger(__name__)


def _context(store, job):
    teacher = require_active_teacher(store, job["createdBy"])
    source = require_source(store, teacher, job["sourceId"])
    member = active_student_membership(store, job["studentMemberId"], job["classId"])
    results = completed_results(store, member["userId"]) if member else None
    if source["classId"] != job["classId"] or results is None:
        fail(409, "SOURCE_CHANGED", solar.ERROR_MESSAGES["SOURCE_CHANGED"])
    return teacher, source, results


def prepare(store, job):
    """요청 잠금 안에서 키·자료·진단·지난 결과를 읽어 Solar 입력을 고정한다."""
    _, source, _ = _context(store, job)
    results = DiagnosticResults.model_validate(job["snapshot"]["diagnosis"])
    key = credentials.unlock(store, job["createdBy"], job["credentialId"])
    material = store.materials.get(job["inputMaterialId"]) if job["inputMaterialId"] else None
    if job["inputMaterialId"] and (
        not material or material["status"] != "published"
        or any(material[field] != job[field] for field in ("classId", "sourceId", "studentMemberId"))
    ):
        fail(409, "SOURCE_CHANGED", solar.ERROR_MESSAGES["SOURCE_CHANGED"])
    passage = _passage(store, job | {"approvedSentenceIds": job["snapshot"].get("approvedSentenceIds")})
    content = material["content"] if material else {}
    lines = passage_lines(passage, set(content.get("hiddenBlockIds", [])))
    notes = [note["text"] for note in content.get("studyNotes", []) if note.get("text")]
    plan = make_plan(diagnostic_tier(results, source["subject"]), last_history(store, job["studentMemberId"], source["subject"]))
    title = " · ".join(v for v in (source.get("title"), source.get("unit")) if v)
    return key, source["subject"], plan, lines, notes, title


def commit(store, job, plan, items, database):
    teacher, source, _ = _context(store, job)
    quiz = create_quiz_draft(store, teacher, QuizDraft(
        sourceId=job["sourceId"], studentMemberId=job["studentMemberId"], inputMaterialId=job["inputMaterialId"],
        approvedSentenceIds=job["snapshot"].get("approvedSentenceIds"),
        title=f"{source['title'][:194]} 확인 문제", questionType="multiple_choice_4",
        questions=[question for question, _ in items],
    ))
    for question, (_, slot) in zip(quiz["questions"], items):
        question.update(category=slot.category, isChallenge=slot.isChallenge)
    quiz["generation"] = {
        "promptVersion": PROMPT_VERSION, "baseLevel": plan.tier.level, "adjustment": plan.adjustment,
        "plan": [{"category": s.category, "isChallenge": s.isChallenge} for s in plan.slots],
    }
    jobs.succeed(store, job, quiz, database)


async def run_quiz_jobs(app, job_ids):
    store = app.state.store
    database = getattr(app.state, "database_connected", False)

    async def locked(operation, *args):
        if database:
            return await run_in_threadpool(operation, *args)
        async with app.state.request_lock:
            return operation(*args)

    def fresh(operation, *args):
        current = load_store(None, include_learning=True) if database else store
        return operation(current, *args)

    for job_id in job_ids:
        async with app.state.generation_slot:
            try:
                job = await locked(jobs.claim, store, job_id, database)
            except APIError:
                logger.warning("Could not claim quiz job %s", job_id)
                continue
            if not job:
                continue
            try:
                key, subject, plan, lines, notes, title = await locked(fresh, prepare, job)
                call = partial(solar.complete_json, key, job["model"])
                items = await run_in_threadpool(generate_items, call, subject, plan, lines, notes, title)
                await locked(fresh, commit, job, plan, items, database)
                continue
            except APIError as error:
                code = error.code if error.code in jobs.QUIZ_ERRORS else "SOURCE_CHANGED"
            except ValidationError:
                code = "SOLAR_OUTPUT_INVALID"
            except Exception as error:
                logger.error("Quiz job %s failed (%s)", job_id, type(error).__name__)
                code = "GENERATION_FAILED"
            try:
                await locked(jobs.failed, store, job_id, code, database)
            except APIError:
                logger.warning("Could not record the outcome of quiz job %s", job_id)
