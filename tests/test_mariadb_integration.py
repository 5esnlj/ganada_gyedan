"""Opt-in integration against a disposable database populated with the repository seed."""

import os

import pytest
from fastapi.testclient import TestClient

from app.database import transaction
from app.main import create_app

pytestmark = pytest.mark.skipif(
    os.getenv("GANADA_TEST_MARIADB") != "1",
    reason="Requires a disposable MariaDB database loaded with the seed SQL",
)
BASE = "http://testserver/api/v1"


def login(client, login_id, role):
    return client.post("/auth/login", json={
        "loginId": login_id, "password": "mock-pass-123", "role": role,
    }).raise_for_status().json()


def test_mariadb_accounts_permissions_and_resumable_diagnostics():
    with TestClient(create_app(), base_url=BASE) as client:
        health = client.get("/health").raise_for_status().json()
        assert health["mode"] == "mariadb" and health["databaseConnected"]
        assert client.post("/auth/login", json={
            "loginId": "t_math", "password": "wrong", "role": "teacher",
        }).status_code == 401

        teacher = login(client, "t_math", "teacher")
        school_class = client.get("/classes").raise_for_status().json()["items"][0]
        class_id = school_class["id"]
        assert class_id.isdecimal() and school_class["teacherSubject"] == "math"
        sources = client.get(f"/classes/{class_id}/sources").raise_for_status().json()["items"]
        assert len(sources) == 1 and sources[0]["subject"] == "math"
        assert client.get(f"/files/{sources[0]['fileId']}/download").raise_for_status().content.startswith(b"%PDF")
        assert client.post("/classes", headers={"X-CSRF-Token": teacher["csrfToken"]}, json={}).status_code == 501

        login(client, "t_science", "teacher")
        assert client.get(f"/sources/{sources[0]['id']}").status_code == 404
        assert {s["subject"] for s in client.get(f"/classes/{class_id}/sources").json()["items"]} == {"science"}

        login(client, "s_a", "student")
        home = client.get(f"/classes/{class_id}/dashboard").raise_for_status().json()
        assert len(home["newMaterials"]) == 2 and len(home["quizzes"]) == 2
        legacy = client.get("/diagnostic/results").raise_for_status().json()
        assert all(part["maxScore"] == 100 for part in legacy["parts"])
        assert all(area["maxScore"] == 200 for area in legacy["areas"])

        student = login(client, "s_pending", "student")
        headers = {"X-CSRF-Token": student["csrfToken"]}
        status = client.get("/diagnostic").raise_for_status().json()
        assert status["availableGradeBands"] == ["g34", "g56"], "Use a fresh seed database"
        client.post("/diagnostic/start", json={"classId": class_id, "gradeBand": "g34"}, headers=headers).raise_for_status()
        first = client.get("/diagnostic/next-question").raise_for_status().json()["question"]
        assert first["sequence"] == 1 and first["audioUrl"] and "correctOptionId" not in first
        assert first["listeningPlaysRemaining"] == 2
        first_answer = {"questionId": first["id"], "selectedOptionId": None}
        client.post("/diagnostic/answers", json=first_answer, headers=headers).raise_for_status()
        play = {"questionId": "g34-l02", "playId": "before-restart"}
        first_play = client.post("/diagnostic/listening-plays", json=play, headers=headers).raise_for_status().json()
        assert first_play == {"remainingPlays": 1}
        cookie = client.cookies["ganada_session"]

    # A new application has no Python memory from the previous process.
    with TestClient(create_app(), base_url=BASE, cookies={"ganada_session": cookie}) as restarted:
        assert restarted.get("/auth/me").raise_for_status().json()["csrfToken"] == headers["X-CSRF-Token"]
        replay = restarted.post("/diagnostic/answers", json=first_answer, headers=headers).raise_for_status().json()
        assert replay["replayed"] and replay["progress"]["completedQuestions"] == 1
        second = restarted.get("/diagnostic/next-question").raise_for_status().json()["question"]
        assert second["id"] == play["questionId"] and second["listeningPlaysRemaining"] == 1
        replay = restarted.post("/diagnostic/listening-plays", json=play, headers=headers).raise_for_status().json()
        assert replay == first_play
        last = restarted.post("/diagnostic/listening-plays", json=play | {"playId": "last-play"}, headers=headers)
        assert last.raise_for_status().json() == {"remainingPlays": 0}
        assert restarted.post("/diagnostic/listening-plays", json=play | {"playId": "extra-play"}, headers=headers).status_code == 409
        for number in range(2, 36):
            question = restarted.get("/diagnostic/next-question").raise_for_status().json()["question"]
            assert question["sequence"] == number
            restarted.post("/diagnostic/answers", json={
                "questionId": question["id"], "selectedOptionId": None,
            }, headers=headers).raise_for_status()
        result = restarted.get("/diagnostic/results").raise_for_status().json()
        assert result["assessmentSummary"]["gradeBand"] == "g34"
        assert result["assessmentSummary"]["communication"]["maxScore"] == 52
        assert all(part["score"] == 0 for part in result["assessmentSummary"]["areaParts"])
        assert len(result["parts"]) == 10 and len(result["areas"]) == 5
        assert restarted.post("/auth/logout", headers=headers).status_code == 204

    with transaction() as cursor:
        cursor.execute("""SELECT d.status, COUNT(a.question_id) AS submitted FROM student_diagnostics d
                          JOIN diagnostic_answers a ON a.student_id = d.student_id
                          WHERE d.student_id = %s GROUP BY d.status""", (student["user"]["id"],))
        assert cursor.fetchone() == {"status": "completed", "submitted": 35}
        cursor.execute("SELECT COUNT(*) AS parts FROM diagnostic_part_results WHERE student_id = %s", (student["user"]["id"],))
        assert cursor.fetchone()["parts"] == 10
