from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from app.diagnostic.assessment import load_assessment_forms
from app.diagnostic.fixtures import demo_provider
from app.diagnostic.forms import DiagnosticForm, StaticFormProvider
from app.main import create_app


BASE = "/api/v1"


def login(app, user: str, role: str = "student") -> TestClient:
    client = TestClient(app, base_url="http://localhost" + BASE)
    result = client.post("/auth/login", json={
        "loginId": user, "password": "mock-pass-123", "role": role,
    })
    assert result.status_code == 200, result.text
    client.headers["X-CSRF-Token"] = result.json()["csrfToken"]
    return client


def answer(client: TestClient, question_id: str, option: str | None):
    return client.post("/diagnostic/answers", json={
        "questionId": question_id, "selectedOptionId": option,
    })


def test_diagnostic_resume_and_explicit_unknown_are_idempotent():
    app = create_app()
    with login(app, "s_pending") as student:
        entry = student.get("/diagnostic/next-question").json()
        assert entry["progress"]["totalQuestions"] == 70
        assert entry["question"]["id"] == "dq_001"
        assert entry["question"]["isSynthetic"]
        assert set(entry["question"]) == {
            "id", "sequence", "area", "skill", "prompt", "options",
            "audioUrl", "stimulusText", "isSynthetic",
        }
        assert answer(student, "dq_002", "1").json()["code"] == "QUESTION_OUT_OF_ORDER"
        assert answer(student, "dq_001", "unexpected").json()["code"] == "OPTION_INVALID"
        assert answer(student, "dq_001", None).json()["progress"]["completedQuestions"] == 1
        assert answer(student, "dq_001", None).json()["replayed"]
        assert answer(student, "dq_001", "1").json()["code"] == "ANSWER_IMMUTABLE"
        assert student.post("/auth/logout").status_code == 204

    with login(app, "s_pending") as resumed:
        entry = resumed.get("/diagnostic/next-question").json()
        assert entry["progress"]["completedQuestions"] == 1
        assert entry["question"]["id"] == "dq_002"
        assert resumed.get("/diagnostic/results").status_code == 409


def test_diagnostic_grading_gates_aggregate_and_completion():
    app = create_app()
    with login(app, "s_pending") as student:
        patterns = (
            (0, 0, 0, 0, 0, 0, 0), (1, 1, 1, 1, 1, 1, 1),
            (1, 0, 0, 0, 0, 0, 1), (1, 1, 1, 1, 0, 0, 0),
            (1, 1, 1, 1, 1, 1, 0), (1, 1, 1, 1, 1, 1, 1),
            (0, 0, 1, 1, 1, 1, 1), (1, 0, 1, 1, 1, 0, 1),
            (1, 0, 1, 0, 1, 1, 1), (1, 0, 1, 1, 0, 0, 0),
        )
        for index, correct in enumerate((value for part in patterns for value in part), 1):
            response = answer(student, f"dq_{index:03}", "1" if correct else "2")
            assert response.status_code == 200, response.text

        completed = response.json()["progress"]
        assert completed["status"] == "completed"
        assert completed["completedQuestions"] == 70
        assert completed["nextQuestionId"] is None
        results = student.get("/diagnostic/results").json()
        assert [(part["score"], part["level"]) for part in results["parts"]] == [
            (0, "below_beginner"), (100, "advanced"), (35, "beginner"), (40, "intermediate"),
            (70, "advanced"), (100, "advanced"), (90, "below_beginner"), (80, "intermediate"),
            (80, "beginner"), (35, "beginner"),
        ]
        assert [(area["score"], area["level"]) for area in results["areas"]] == [
            (100, "beginner"), (75, "beginner"), (170, "advanced"),
            (170, "advanced"), (115, "intermediate"),
        ]
        assert student.post("/diagnostic/start", json={"classId": "cls_1"}).json() == completed
        assert student.get("/diagnostic/next-question").json()["question"] is None
        assert answer(student, "dq_070", "2").json()["replayed"]
        assert student.get("/classes/cls_1/materials").status_code == 200


def test_diagnostic_start_pins_form_and_requires_active_membership():
    provider = demo_provider()
    app = create_app(diagnostic_form_provider=provider)
    with login(app, "s_a") as student:
        assert student.get("/diagnostic").json()["status"] == "not_started"
        started = student.post("/diagnostic/start", json={"classId": "cls_1"})
        assert started.status_code == 200, started.text
        assert started.json()["formId"] == "form_mock_v1"

        replacement = provider.form.model_copy(update={"id": "next_form"})
        app.state.diagnostic_form_provider = replace(provider, form=replacement)
        assert student.post("/diagnostic/start", json={"classId": "cls_1"}).json()["formId"] == "form_mock_v1"
        assert student.get("/diagnostic/next-question").json()["question"]["id"] == "dq_001"

    with login(app, "s_new") as unjoined:
        assert unjoined.post("/diagnostic/start", json={"classId": "cls_1"}).status_code == 404
        assert unjoined.get("/diagnostic/next-question").json()["code"] == "CLASS_REQUIRED"


def test_start_without_published_form_does_not_create_attempt():
    app = create_app(diagnostic_form_provider=StaticFormProvider(None))
    app.state.diagnostic_assessment_forms = {}
    with login(app, "s_a") as student:
        unavailable = student.post("/diagnostic/start", json={"classId": "cls_1"})
        assert unavailable.status_code == 503
        assert unavailable.json()["code"] == "DIAGNOSTIC_FORM_UNAVAILABLE"
        assert student.post("/diagnostic/start", json={"classId": "cls_1", "gradeBand": "g34"}).status_code == 503
        assert student.get("/diagnostic").json() == {
            "status": "not_started", "formId": None, "totalQuestions": 0,
            "completedQuestions": 0, "nextQuestionId": None, "availableGradeBands": [],
        }


def test_teacher_roster_permissions_and_incomplete_results():
    app = create_app()
    with login(app, "t_science", "teacher") as teacher:
        rows = teacher.get("/classes/cls_1/students").json()["items"]
        roster = {row["id"]: row for row in rows}
        assert len(roster["mem_s_a"]["partResults"]) == 10
        assert len(roster["mem_s_a"]["areaResults"]) == 5
        assert roster["mem_s_pending"]["diagnosticStatus"] == "in_progress"
        assert roster["mem_s_pending"]["partResults"] == []
        assert teacher.get("/classes/cls_1/students/mem_s_outside/diagnostic").status_code == 404
        assert teacher.get("/classes/cls_2/students").status_code == 404
        assert teacher.get("/classes/cls_1/students/mem_s_pending/diagnostic").json()["results"] is None

    with login(app, "s_a") as student:
        assert student.get("/classes/cls_1/students").status_code == 403


def test_form_rejects_incomplete_rubric_groups():
    form = demo_provider().form
    data = form.model_dump()
    data["questions"] = data["questions"][:-1]
    with pytest.raises(ValidationError, match="must total 100 points"):
        DiagnosticForm.model_validate(data)


@pytest.mark.parametrize(("band", "other", "first_answer"), [("g34", "g56", "2"), ("g56", "g34", "4")])
def test_attached_assessment_uses_pinned_form_and_source_scoring(band, other, first_answer):
    app = create_app()
    form = app.state.diagnostic_assessment_forms[band]
    with login(app, "s_pending") as student:
        initial = student.get("/diagnostic").json()
        assert initial["formId"] == "form_mock_v1"
        assert initial["availableGradeBands"] == ["g34", "g56"]
        started = student.post("/diagnostic/start", json={"classId": "cls_1", "gradeBand": band})
        assert started.status_code == 200, started.text
        assert started.json() == {
            "status": "in_progress", "formId": f"official-{band}-v1",
            "totalQuestions": 35, "completedQuestions": 0,
            "nextQuestionId": f"{band}-l01", "gradeBand": band,
        }
        repeated = student.post("/diagnostic/start", json={"classId": "cls_1", "gradeBand": other})
        assert repeated.json()["gradeBand"] == band
        assert student.get("/diagnostic").json()["availableGradeBands"] == []
        first = student.get("/diagnostic/next-question").json()["question"]
        assert first["skill"] == "listening" and first["sectionNumber"] == 1
        assert first["sectionTotal"] == 15 and first["maxPoints"] == 4
        assert first["audioUrl"].startswith(f"/diagnostic-assets/{band}/")
        assert first["isSynthetic"] is False
        assert "correctOptionId" not in first
        assert "tier" not in first
        assert answer(student, f"{other}-l01", "1").status_code == 409
        assert answer(student, first["id"], first_answer).status_code == 200
        assert answer(student, first["id"], first_answer).json()["replayed"]
        second = student.get("/diagnostic/next-question").json()["question"]
        assert second["id"] == f"{band}-l02"
        assert len(second["options"]) == 4
        assert all(option["imageUrl"].startswith(f"/diagnostic-assets/{band}/") for option in second["options"])
        assert student.post("/auth/logout").status_code == 204

    with login(app, "s_pending") as resumed:
        next_item = resumed.get("/diagnostic/next-question").json()
        assert next_item["progress"]["gradeBand"] == band
        assert next_item["progress"]["completedQuestions"] == 1
        assert next_item["question"]["id"] == f"{band}-l02"

        for item in form.questions[1:15]:
            response = answer(resumed, item.display.id, item.correct_option_id)
            assert response.status_code == 200, response.text
        reading = resumed.get("/diagnostic/next-question").json()["question"]
        assert reading["id"] == f"{band}-r01"
        assert reading["sectionNumber"] == 1 and reading["sectionTotal"] == 20
        assert reading["imageUrl"].startswith(f"/diagnostic-assets/{band}/")

        for item in form.questions[15:]:
            response = answer(resumed, item.display.id, item.correct_option_id)
            assert response.status_code == 200, response.text
        assert response.json()["progress"]["status"] == "completed"
        assert response.json()["progress"]["completedQuestions"] == 35
        assert resumed.get("/diagnostic").json()["availableGradeBands"] == []

        results = resumed.get("/diagnostic/results").json()
        assert results["isSynthetic"] is False
        assert len(results["parts"]) == 10 and len(results["areas"]) == 5
        assert all(part["score"] == part["maxScore"] == 100 for part in results["parts"])
        assert all(area["score"] == area["maxScore"] == 200 for area in results["areas"])
        summary = results["assessmentSummary"]
        assert summary["gradeBand"] == band
        assert summary["listening"] == {"score": 100, "maxScore": 100, "percent": 100, "level": "advanced"}
        assert summary["reading"] == summary["listening"]
        assert summary["overall"] == {"score": 200, "maxScore": 200, "level": "advanced"}
        assert all(subject["level"] == "advanced" for subject in summary["subjects"])
        assert summary["communication"] == {
            "score": 52, "maxScore": 52, "percent": 100, "questionCount": 11, "status": "adequate",
        }
        assert resumed.get("/classes/cls_1/dashboard").status_code == 200

    with login(app, "t_math", "teacher") as teacher:
        details = teacher.get("/classes/cls_1/students/mem_s_pending/diagnostic").json()
        assert details["results"]["assessmentSummary"] == summary


def test_attached_assessment_respects_raw_point_criterion_and_failed_thresholds():
    from app.diagnostic.grading import grade

    form = load_assessment_forms()["g34"]
    answers = {
        item.display.id: (
            item.correct_option_id if item.display.area == "korean" and item.tier != "beginner" else None
        )
        for item in form.questions
    }
    results = grade(form, answers)
    summary = results.assessmentSummary
    korean = summary.subjects[0]
    assert korean.area == "korean"
    assert korean.percent > 70
    assert korean.scoreLevel == "advanced"
    assert korean.criterionLevel == "below_beginner"
    assert korean.level == "below_beginner"
    assert korean.tierCounts["beginner"].correct == 0
    assert korean.tierCounts["advanced"].correct == 2
    assert summary.overall.level == "below_beginner"
    assert summary.communication.status == "needs_improvement"
    assert results.areas[0].score == 2 * korean.percent
    assert results.areas[0].level == korean.level


def test_started_legacy_attempt_cannot_be_replaced_by_grade_selection():
    app = create_app()
    with login(app, "s_pending") as student:
        assert answer(student, "dq_001", "1").status_code == 200
        assert student.get("/diagnostic").json()["availableGradeBands"] == []
        response = student.post("/diagnostic/start", json={"classId": "cls_1", "gradeBand": "g34"})
        assert response.status_code == 200
        assert response.json()["formId"] == "form_mock_v1"
        assert response.json()["completedQuestions"] == 1
        assert "gradeBand" not in response.json()


def test_assessment_media_are_bundled_in_the_matching_grade_area_and_skill():
    public = Path(__file__).resolve().parents[1] / "frontend" / "public"
    assets = set()
    for band, form in load_assessment_forms().items():
        for item in form.questions:
            question = item.display
            prefix = f"/diagnostic-assets/{band}/{question.area}/{question.skill}/"
            assert bool(question.audioUrl) == (question.skill == "listening")
            urls = [question.audioUrl, question.imageUrl, *(option.imageUrl for option in question.options)]
            for url in filter(None, urls):
                assert url.startswith(prefix), url
                file = public / url.lstrip("/")
                assert file.is_file(), file
                assets.add(file)

    assert len([asset for asset in assets if asset.suffix == ".mp3"]) == 30
    assert len([asset for asset in assets if asset.suffix == ".png"]) == 23
