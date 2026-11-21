"""Cross-slice regressions for the endpoints used by the supplied frontend."""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


PASSWORD = "mock-pass-123"
LEVELS = {"below_beginner", "beginner", "intermediate", "advanced", "not_diagnosed"}


@pytest.fixture
def browser():
    app = create_app()
    clients = []

    def open_session(login_id=None):
        client = TestClient(app, base_url="http://testserver/api/v1")
        clients.append(client)
        if login_id:
            role = "teacher" if login_id.startswith("t_") else "student"
            response = client.post(
                "/auth/login", json={"loginId": login_id, "role": role, "password": PASSWORD}
            )
            assert response.status_code == 200, response.text
            client.headers["X-CSRF-Token"] = response.json()["csrfToken"]
            assert "httponly" in response.headers["Set-Cookie"].lower()
        return client

    yield open_session

    for client in clients:
        client.close()


def read(client, path, **kwargs):
    response = client.get(path, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def assert_api_error(response, expected_status, expected_code=None):
    assert response.status_code == expected_status, response.text
    body = response.json()
    assert {"code", "message", "requestId", "details"} <= body.keys()
    assert isinstance(body["requestId"], str) and body["requestId"]
    if expected_code is not None:
        assert body["code"] == expected_code


def test_teacher_dashboard_keeps_diagnostic_area_separate_from_subject(browser):
    teacher = browser("t_math")
    me = read(teacher, "/auth/me")
    assert me["user"]["role"] == "teacher"
    assert me["csrfToken"] == teacher.headers["X-CSRF-Token"]
    assert me["nextAction"] == "classes"

    classes = read(teacher, "/classes")
    assert classes["nextCursor"] is None
    entry = next(row for row in classes["items"] if row["id"] == "cls_1")
    assert entry["teacherSubject"] == "math"

    math_dashboard = read(teacher, "/classes/cls_1/dashboard")
    science_dashboard = read(teacher, "/classes/cls_1/dashboard", params={"area": "science"})
    assert math_dashboard["selectedArea"] == "math"
    assert science_dashboard["selectedArea"] == "science"
    assert science_dashboard["className"] == entry["name"]
    assert science_dashboard["teacherSubject"] == "math"
    assert science_dashboard["pendingReviewQuizSetCount"] == math_dashboard["pendingReviewQuizSetCount"]
    assert science_dashboard["levelCounts"].keys() == LEVELS
    assert sum(science_dashboard["levelCounts"].values()) == science_dashboard["studentCount"]

    rows = {row["studentMemberId"]: row for row in science_dashboard["students"]}
    assert rows["mem_s_a"]["diagnosticStatus"] == "completed"
    assert all(part["area"] == "science" for part in rows["mem_s_a"]["parts"])
    assert rows["mem_s_a"]["aggregate"]["area"] == "science"
    assert rows["mem_s_a"]["latestQuiz"]["quizId"] == "quiz_a_math_1"
    assert rows["mem_s_pending"]["diagnosticStatus"] == "in_progress"
    assert rows["mem_s_pending"]["parts"] == []
    assert rows["mem_s_pending"]["aggregate"] is None

    activity = read(teacher, "/classes/cls_1/activity")
    assert {row["quizId"] for row in activity["items"]} == {"quiz_a_math_1"}


@pytest.mark.parametrize(("login_id", "subject"), [("t_math", "math"), ("t_science", "science")])
def test_teacher_material_cards_consume_original_sources(browser, login_id, subject):
    teacher = browser(login_id)
    sources = read(teacher, "/classes/cls_1/sources")
    assert sources["nextCursor"] is None
    assert len(sources["items"]) == 1
    source = sources["items"][0]
    assert source["id"] == f"src_{subject}_1"
    assert source["subject"] == subject
    assert isinstance(source["unit"], str)
    assert isinstance(source["title"], str)
    assert dt.datetime.fromisoformat(source["createdAt"]).tzinfo is not None


def test_student_dashboard_counts_are_driven_by_saved_attempts(browser):
    student = browser("s_a")
    teacher = browser("t_math")
    path = "/classes/cls_1/dashboard"
    math_quiz = "/quizzes/quiz_a_math_1"

    def home(area=None):
        params = {"area": area} if area else None
        return read(student, path, params=params)

    def answer(index, choice, key):
        response = student.post(
            f"{math_quiz}/answers",
            headers={"Idempotency-Key": key},
            json={"questionId": f"quiz_a_math_1_q{index}", "selectedOptionId": choice},
        )
        assert response.status_code == 200, response.text
        return response.json()

    def teacher_latest():
        dashboard = read(teacher, path)
        return next(
            row["latestQuiz"] for row in dashboard["students"] if row["studentMemberId"] == "mem_s_a"
        )

    daily = home()
    math = home("math")
    assert daily["selectedArea"] == daily["aggregate"]["area"] == "daily"
    assert math["selectedArea"] == math["aggregate"]["area"] == "math"
    assert {q["subject"] for q in daily["quizzes"]} == {"math", "science"}
    assert math["unansweredQuestionCount"] == daily["unansweredQuestionCount"] == 6
    cards = {material["id"]: material for material in daily["newMaterials"]}
    math_card = cards["mat_a_math_1"]
    source = read(teacher, "/sources/src_math_1")
    assert math_card["unit"] == source["unit"]
    assert math_card["unitNumber"] is None

    answer(1, "2", "first-1")
    in_progress = teacher_latest()
    assert in_progress["status"] == "in_progress"
    assert in_progress["firstScore"] is None
    assert home()["unansweredQuestionCount"] == 5

    answer(2, "1", "first-2")
    answer(3, "1", "first-3")
    complete = teacher_latest()
    assert complete["status"] == "completed"
    assert complete["firstCorrectCount"] == 2
    assert home()["retryQuestionCount"] == 1
    assert home()["unansweredQuestionCount"] == 3

    retry = answer(1, "1", "retry-1")
    assert retry["retryCount"] == 1
    corrected = teacher_latest()
    assert corrected["firstScore"] == complete["firstScore"]
    assert corrected["firstCorrectCount"] == 2
    assert corrected["latestCorrectCount"] == 3
    assert corrected["currentWrongCount"] == 0
    assert home()["retryQuestionCount"] == 0


def test_frontend_session_and_learning_gates(browser):
    path = "/classes/cls_1/dashboard"
    anonymous = browser()
    assert_api_error(anonymous.get(path), 401)

    pending = browser("s_pending")
    assert read(pending, "/auth/me")["nextAction"] == "diagnostic"
    assert_api_error(pending.get(path), 403, "DIAGNOSTIC_REQUIRED")
    assert read(browser("s_new"), "/auth/me")["nextAction"] == "join_class"

    teacher = browser("t_math")
    removed = teacher.post("/auth/logout")
    assert removed.status_code == 204
    assert removed.content == b""
    assert_api_error(teacher.get("/auth/me"), 401)
