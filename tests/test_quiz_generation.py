"""확인 문제 생성: 출제 기준, Solar 응답 검증, 교사 검토·배포와 학생 풀이까지 (Upstage에 연결하지 않음)."""

from io import BytesIO
import os
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter
import pytest

from app.diagnostic.dto import AssessmentSummary, DiagnosticResults
from app.generation import solar
from app.main import create_app
from app.quizzes.generation import ADJUST_DOWN, ADJUST_UP, History, diagnostic_tier, make_plan, prompt_data

AREAS = ("korean", "math", "social", "science", "daily")
PASSWORD = "mock-pass-123"


def results(levels: dict, reading: dict | None = None) -> DiagnosticResults:
    reading = reading or {}
    return DiagnosticResults(
        formId="form_test", isSynthetic=True,
        parts=[
            {"area": a, "skill": s, "score": 50, "level": reading.get(a, levels[a]) if s == "reading" else levels[a]}
            for a in AREAS for s in ("listening", "reading")
        ],
        areas=[{"area": a, "score": 100, "level": levels[a]} for a in AREAS],
    )


def test_base_level_is_lower_of_subject_and_subject_reading():
    levels = {"korean": "advanced", "math": "intermediate", "social": "intermediate", "science": "beginner", "daily": "beginner"}
    tier = diagnostic_tier(results(levels, {"social": "advanced", "math": "beginner"}), "social")
    assert (tier.level, tier.communication) == ("intermediate", "needs_improvement")
    assert diagnostic_tier(results(levels, {"math": "beginner"}), "math").level == "beginner"


def test_quiz_uses_subject_cell_and_prefers_explicit_communication_status():
    assessment = results(dict.fromkeys(AREAS, "beginner") | {"social": "intermediate"}, {"social": "beginner"})
    skill = {"score": 10, "maxScore": 20, "percent": 50, "level": "intermediate"}
    assessment.assessmentSummary = AssessmentSummary.model_validate({
        "gradeBand": "g34", "listening": skill, "reading": skill, "subjects": [],
        "overall": {"score": 100, "maxScore": 200, "level": "intermediate"},
        "communication": {"score": 5, "maxScore": 10, "percent": 50, "questionCount": 10, "status": "adequate"},
    })
    plan = make_plan(diagnostic_tier(assessment, "social"), None)
    payload = prompt_data("social", plan, [("s1", "재판은 갈등을 해결하는 제도입니다.")], [], "재판")
    assert payload["student"]["baseLevel"] == "초급"
    subject = payload["rubric"]["subjectCriterion"]
    assert subject["grade"] == "중급" and "배경지식" in subject["guidance"]
    assert payload["rubric"]["communicationCriterion"]["label"] == "양호"
    assert len(payload["plan"]) == 5 and payload["challengeLevel"] == "중급"


def test_plan_follows_criteria_and_adjusts_challenge():
    tier = diagnostic_tier(results(dict.fromkeys(AREAS, "beginner")), "math")
    base = make_plan(tier, None)
    assert [(s.category, s.isChallenge) for s in base.slots] == [
        ("term", False), ("term", False), ("fact", False), ("fact", False), ("relation", True),
    ]
    up = make_plan(tier, History(current_wrong=0, challenge_all_correct=True))
    assert up.adjustment == ADJUST_UP and sum(s.isChallenge for s in up.slots) == 2
    down = make_plan(tier, History(current_wrong=2, challenge_all_correct=False))
    assert down.adjustment == ADJUST_DOWN and not any(s.isChallenge for s in down.slots)


def item(answer, category, challenge=False, sources=("s1",)):
    return {
        "prompt": "무엇일까요?", "choices": ["가", "나", "다", "라"], "answer": answer,
        "category": category, "isChallenge": challenge, "sourceSentenceIds": list(sources),
        "explanation": "해설입니다.",
    }


@pytest.fixture(params=[False, pytest.param(True, marks=pytest.mark.skipif(
    os.getenv("GANADA_TEST_MARIADB") != "1", reason="Requires disposable seeded MariaDB",
))])
def teacher(monkeypatch, request, tmp_path):
    if not request.param:
        monkeypatch.delenv("GANADA_DB_HOST", raising=False)
    monkeypatch.setenv("GANADA_SOLAR_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("GANADA_DB_FILES_DIR", str(tmp_path))
    app = create_app()
    with TestClient(app, base_url="http://testserver/api/v1") as client:
        login = client.post("/auth/login", json={"loginId": "t_math", "password": PASSWORD, "role": "teacher"})
        client.headers["X-CSRF-Token"] = login.raise_for_status().json()["csrfToken"]
        yield app, client


def student_client(app, login_id="s_a"):
    client = TestClient(app, base_url="http://testserver/api/v1")
    login = client.post("/auth/login", json={"loginId": login_id, "password": PASSWORD, "role": "student"})
    client.headers["X-CSRF-Token"] = login.raise_for_status().json()["csrfToken"]
    return client


def target(client, name):
    class_id = client.get("/classes").raise_for_status().json()["items"][0]["id"]
    base = f"/classes/{class_id}"
    source = next(s for s in client.get(base + "/sources").raise_for_status().json()["items"] if "비와 비율" in s["title"])
    student = next(s for s in client.get(base + "/students").raise_for_status().json()["items"] if name in s["displayName"])
    body = {"operation": "generate_quiz", "sourceId": source["id"], "studentMemberIds": [student["id"]]}
    return base + "/generation-jobs", body, source


def latest_job(client, endpoint):
    return client.get(endpoint, params={"operation": "generate_quiz"}).raise_for_status().json()["items"][0]


def test_generate_review_publish_and_solve(teacher, monkeypatch):
    app, client = teacher
    calls = []

    def fake_solar(api_key, model, instructions, data):
        assert api_key == "not-a-live-key-wxyz" and "~일까요?" in instructions
        calls.append(data)
        assert "areas" not in data["student"]
        return {"items": [item((1, 2, 3, 4, 2)[i], slot["category"], slot["isChallenge"],
                               (data["sentences"][0]["id"],)) for i, slot in enumerate(data["plan"])]}

    monkeypatch.setattr(solar, "complete_json", fake_solar)
    endpoint, body, _ = target(client, "학생 A")
    if not app.state.database_connected:
        assert client.post(endpoint, json=body, headers={"Idempotency-Key": "q1"}).status_code == 409
    client.post("/ai-credentials", json={"apiKey": "not-a-live-key-wxyz"}).raise_for_status()
    assert client.post(endpoint, json=body | {"questionType": "true_false"}, headers={"Idempotency-Key": "tf"}).status_code == 422

    _, unpublished_target, _ = target(client, "학생 B")
    mixed_targets = body | {"studentMemberIds": body["studentMemberIds"] + unpublished_target["studentMemberIds"]}
    missing = client.post(endpoint, json=mixed_targets, headers={"Idempotency-Key": "unpublished-passage"})
    assert missing.status_code == 422 and missing.json()["code"] == "QUIZ_PASSAGE_REQUIRED"
    assert "자료 제작에서 먼저 전환본을 배포" in missing.json()["message"]
    assert not calls

    created = client.post(endpoint, json=body, headers={"Idempotency-Key": "q1"})
    assert created.status_code == 202, created.text
    job = latest_job(client, endpoint)
    assert job["status"] == "succeeded", job
    assert job["inputMaterialId"] is not None
    assert client.get(f"/generation-jobs/{job['id']}").raise_for_status().json()["resultId"] == job["quizId"]
    data = calls[0]
    assert data["student"]["baseLevel"] == "고급" and data["sentences"] == [{"id": "s1", "text": "쉽게 바꾼 가상 학습자료입니다."}]
    assert data["rubric"]["subjectCriterion"]["textTreatment"] == "preserve"
    assert "개념어" in data["rubric"]["subjectCriterion"]["guidance"]
    assert data["rubric"]["communicationCriterion"]["label"] == "양호"
    assert [slot["category"] for slot in data["plan"]] == ["term", "relation", "application", "application", "comparison"]

    replay = client.post(endpoint, json=body, headers={"Idempotency-Key": "q1"}).raise_for_status().json()
    assert replay["items"][0]["id"] == job["id"] and len(calls) == 1

    quiz_path = f"/quizzes/{job['quizId']}"
    quiz = client.get(quiz_path)
    detail = quiz.raise_for_status().json()
    assert detail["status"] == "draft" and len(detail["questions"]) == 5
    assert detail["generation"]["baseLevel"] == "advanced" and detail["passage"]["paragraphs"]
    assert detail["questions"][-1]["category"] == "comparison" and detail["questions"][-1]["isChallenge"]

    etag = quiz.headers["ETag"]
    student = student_client(app)
    assert student.get(quiz_path).status_code == 404
    question = detail["questions"][0]
    patch = {field: question[field] for field in ("prompt", "choices", "correctOptionId", "explanation", "sourceSentenceIds", "maxPoints")}
    edited = client.patch(f"{quiz_path}/questions/{question['id']}", json=patch | {"explanation": "교사 수정 해설"},
                          headers={"If-Match": etag}).raise_for_status()
    assert client.post(f"{quiz_path}/review", headers={"If-Match": etag}).status_code == 412
    reviewed = client.post(f"{quiz_path}/review", headers={"If-Match": edited.headers["ETag"]})
    published = client.post(f"{quiz_path}/publish", headers={"If-Match": reviewed.raise_for_status().headers["ETag"]})
    published.raise_for_status()

    mine = student.get(quiz_path).raise_for_status().json()
    assert mine["questions"][-1]["isChallenge"] and "category" not in mine["questions"][0]
    assert "correctOptionId" not in mine["questions"][0]
    explanation_path = f"{quiz_path}/questions/{mine['questions'][0]['id']}/explanation"
    assert student.get(explanation_path).status_code == 403
    for q in mine["questions"]:
        student.post(f"{quiz_path}/answers", json={"questionId": q["id"], "selectedOptionId": "1"},
                     headers={"Idempotency-Key": f"a-{q['id']}"}).raise_for_status()
    explanation = student.get(explanation_path).raise_for_status().json()
    assert explanation["sourceSentenceIds"] == ["s1"]
    assert explanation["explanation"] == "교사 수정 해설"

    result = client.get(quiz_path).raise_for_status().json()
    assert [q["firstAttemptCorrect"] for q in result["questions"]] == [True, False, False, False, False]
    assert result["generation"]["nextAdjustment"] == ADJUST_DOWN

    # 다음 확인 문제는 지난 첫 풀이 결과를 반영해 도전 문항을 뺀다.
    client.post(endpoint, json=body, headers={"Idempotency-Key": "q2"}).raise_for_status()
    assert not any(slot["isChallenge"] for slot in calls[1]["plan"])

    question_id = mine["questions"][1]["id"]
    attempt = {"questionId": question_id, "selectedOptionId": "2"}
    headers = {"Idempotency-Key": "retry-2"}
    retry = student.post(f"{quiz_path}/answers", json=attempt, headers=headers).raise_for_status().json()
    assert retry["results"]["firstScore"] == 1 and retry["results"]["currentWrongCount"] == 3
    with TestClient(create_app() if app.state.database_connected else app, base_url="http://testserver/api/v1") as restarted:
        login = restarted.post("/auth/login", json={"loginId": "s_a", "password": PASSWORD, "role": "student"})
        restarted.headers["X-CSRF-Token"] = login.raise_for_status().json()["csrfToken"]
        replay = restarted.post(f"{quiz_path}/answers", json=attempt, headers=headers).raise_for_status().json()
        assert replay["replayed"] and replay["attemptNo"] == 2 and replay["results"]["firstScore"] == 1


def test_invalid_solar_output_fails_the_job(teacher, monkeypatch):
    _, client = teacher
    monkeypatch.setattr(solar, "complete_json", lambda *args: {"items": [item(1, "term")]})
    client.post("/ai-credentials", json={"apiKey": "not-a-live-key-wxyz"}).raise_for_status()
    endpoint, body, source = target(client, "학생 B")
    client.post(endpoint, json=body | {"approvedSentenceIds": [source["sentences"][0]["id"]]},
                headers={"Idempotency-Key": "bad"}).raise_for_status()
    job = latest_job(client, endpoint)
    assert job["status"] == "failed" and job["errorCode"] == "SOLAR_OUTPUT_INVALID" and job["quizId"] is None


def test_original_quiz_limits_passage_to_approved_sentences(teacher, monkeypatch):
    app, client = teacher
    endpoint, body, _ = target(client, "학생 B")
    fixture = Path(__file__).resolve().parents[1] / "app/fixtures/sample.pdf"
    page = PdfReader(fixture).pages[0]
    pdf = PdfWriter()
    pdf.add_page(page)
    pdf.add_page(page)
    data = BytesIO()
    pdf.write(data)
    url = endpoint.replace("generation-jobs", "sources/pdf")
    source = client.post(url, data={"title": "교사 승인 지문"}, files={
        "file": ("source.pdf", data.getvalue(), "application/pdf"),
    }).raise_for_status().json()
    approved, excluded = source["sentences"][:2]
    assert approved["id"] != excluded["id"]
    body.update(sourceId=source["id"], useStudentMaterials=False)
    client.post("/ai-credentials", json={"apiKey": "not-a-live-key-wxyz"}).raise_for_status()

    def fake_solar(_key, _model, _instructions, payload):
        assert payload["sentences"] == [approved]
        return {"items": [item(1, slot["category"], slot["isChallenge"], (approved["id"],)) for slot in payload["plan"]]}

    monkeypatch.setattr(solar, "complete_json", fake_solar)
    headers = {"Idempotency-Key": "approved-passage"}
    missing = client.post(endpoint, json=body, headers=headers)
    assert missing.status_code == 422 and missing.json()["code"] == "QUIZ_PASSAGE_REQUIRED"
    client.post(endpoint, json=body | {"approvedSentenceIds": [approved["id"]]}, headers=headers).raise_for_status()
    job = latest_job(client, endpoint)
    assert job["status"] == "succeeded" and job["inputMaterialId"] is None, job
    path = f"/quizzes/{job['quizId']}"
    quiz = client.get(path).raise_for_status()
    reviewed = client.post(f"{path}/review", headers={"If-Match": quiz.headers["ETag"]}).raise_for_status()
    client.post(f"{path}/publish", headers={"If-Match": reviewed.headers["ETag"]}).raise_for_status()
    student = student_client(app, "s_b")
    assert student.get(path).raise_for_status().json()["passage"]["paragraphs"] == [approved]
    assert student.get(f"/files/{source['fileId']}/download").status_code == 404


def test_requires_diagnosed_student_in_class(teacher):
    app, client = teacher
    client.post("/ai-credentials", json={"apiKey": "not-a-live-key-wxyz"}).raise_for_status()
    endpoint, body, _ = target(client, "s_pending")
    if not app.state.database_connected:
        pending = client.post(endpoint, json=body, headers={"Idempotency-Key": "p"})
        assert pending.status_code == 409 and pending.json()["code"] == "STUDENT_DIAGNOSTIC_REQUIRED"
    outside = client.post(endpoint, json=body | {"studentMemberIds": ["0"]},
                          headers={"Idempotency-Key": "o"})
    assert outside.status_code == 404
