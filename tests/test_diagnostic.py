from dataclasses import replace

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

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
    with login(app, "s_a") as student:
        unavailable = student.post("/diagnostic/start", json={"classId": "cls_1"})
        assert unavailable.status_code == 503
        assert unavailable.json()["code"] == "DIAGNOSTIC_FORM_UNAVAILABLE"
        assert student.get("/diagnostic").json() == {
            "status": "not_started", "formId": None, "totalQuestions": 0,
            "completedQuestions": 0, "nextQuestionId": None,
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
