from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.classes.service import invitation_digest
from app.core.access import COOKIE_NAME, session_digest
from app.main import create_app

PREFIX = "/api/v1"


@pytest.fixture
def session():
    app = create_app()
    clients = []

    def make_client(login_id=None, role=None, password="mock-pass-123"):
        client = TestClient(app, base_url=f"http://testserver{PREFIX}")
        clients.append(client)
        if login_id is None:
            return client
        role = role or ("teacher" if login_id.startswith("t_") else "student")
        result = client.post("/auth/login", json={
            "loginId": login_id,
            "password": password,
            "role": role,
        })
        if result.is_success:
            client.headers["X-CSRF-Token"] = result.json()["csrfToken"]
        return client, result

    yield app.state.store, make_client

    for client in clients:
        client.close()


def test_role_validation_csrf_and_session_expiration(session):
    state, make_client = session
    for login_id, role, password in (
        ("t_math", "teacher", "incorrect"),
        ("s_a", "teacher", "mock-pass-123"),
    ):
        rejected, response = make_client(login_id, role, password)
        assert response.status_code == 401
        assert rejected.cookies.get(COOKIE_NAME) is None

    state.users["usr_t_math"]["verified"] = False
    _, unverified = make_client("t_math")
    assert unverified.status_code == 401
    state.users["usr_t_math"]["verified"] = True

    teacher, response = make_client("t_math")
    assert response.status_code == 200, response.text
    assert set(response.json()["user"]) == {"id", "loginId", "role", "displayName", "nativeLanguage"}
    assert "httponly" in response.headers["set-cookie"].lower()
    token = teacher.cookies[COOKIE_NAME]
    key = session_digest(token)
    assert token not in state.sessions
    assert state.sessions[key]["userId"] == "usr_t_math"

    csrf = teacher.headers.pop("X-CSRF-Token")
    assert teacher.post("/auth/logout").json()["code"] == "CSRF_INVALID"
    teacher.headers["X-CSRF-Token"] = csrf
    state.sessions[key]["expiresAt"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    expired = teacher.get("/auth/me")
    assert expired.status_code == 401
    assert expired.json()["code"] == "SESSION_INVALID"
    assert key not in state.sessions

    teacher, _ = make_client("t_math")
    active_key = session_digest(teacher.cookies[COOKIE_NAME])
    assert teacher.post("/auth/logout").status_code == 204
    assert teacher.cookies.get(COOKIE_NAME) is None
    assert active_key not in state.sessions


def test_korean_demo_teacher_and_student_can_select_their_classes(session):
    _, make_client = session
    teacher, _ = make_client("t_korean")
    student, _ = make_client("s_a")
    teacher_classes = teacher.get("/classes").raise_for_status().json()["items"]
    student_classes = student.get("/classes").raise_for_status().json()["items"]
    assert {row["id"]: row["teacherSubject"] for row in teacher_classes} == {
        "cls_1": "korean",
        "cls_reading": "korean",
    }
    assert {row["id"] for row in student_classes} == {"cls_1", "cls_reading"}
    assert student.get("/classes/cls_reading/dashboard").status_code == 200

    sources = teacher.get("/classes/cls_1/sources").raise_for_status().json()["items"]
    assert [(source["id"], source["subject"]) for source in sources] == [("src_korean_1", "korean")]
    pdf = teacher.get(f"/files/{sources[0]['fileId']}/download").raise_for_status()
    assert pdf.content.startswith(b"%PDF")
    assert teacher.get("/sources/src_math_1").status_code == 404


def test_open_class_and_invite_with_owner_assigned_subject(session):
    state, make_client = session
    owner, _ = make_client("t_math")
    science, _ = make_client("t_science")
    outside, _ = make_client("t_outside")
    student, _ = make_client("s_a")
    class_data = {"name": "가상 새 반", "teacherSubject": "math"}
    assert student.post("/classes", json=class_data).status_code == 403
    result = owner.post("/classes", json=class_data)
    assert result.status_code == 201, result.text
    classroom = result.json()
    assert classroom["ownerId"] == "usr_t_math"
    assert state.memberships[classroom["membershipId"]]["teacherSubject"] == "math"
    assert owner.get("/classes").json()["items"][0] == classroom

    invite = {"invitedRole": "teacher", "teacherSubject": "science"}
    assert science.post("/classes/cls_1/invitations", json=invite).status_code == 403
    assert owner.post("/classes/cls_1/invitations", json={"invitedRole": "teacher"}).status_code == 422
    result = owner.post("/classes/cls_1/invitations", json=invite)
    assert result.status_code == 201, result.text
    code = result.json()["code"]
    assert code not in state.invitations
    assert "code" not in state.invitations[invitation_digest(code)]
    assert outside.post("/invitations/accept", json={
        "code": code, "teacherSubject": "math",
    }).status_code == 422

    joined = outside.post("/invitations/accept", json={"code": code})
    assert joined.status_code == 200, joined.text
    assert joined.json()["membership"]["teacherSubject"] == "science"
    assert {item["id"] for item in outside.get("/classes").json()["items"]} == {"cls_1", "cls_2"}


def test_student_invitation_pins_role_capacity_and_active_membership(session):
    state, make_client = session
    teacher, _ = make_client("t_science")
    student, result = make_client("s_new")
    assert result.json()["nextAction"] == "join_class"
    result = teacher.post("/classes/cls_1/invitations", json={"invitedRole": "student"})
    assert result.status_code == 201, result.text
    code = result.json()["code"]
    invite = state.invitations[invitation_digest(code)]

    wrong_role = teacher.post("/invitations/accept", json={"code": code})
    assert wrong_role.status_code == 403
    assert invite["usedCount"] == 0
    joined = student.post("/invitations/accept", json={"code": code}).json()
    assert joined["nextAction"] == "diagnostic"
    assert joined["alreadyMember"] is False
    assert joined["membership"]["teacherSubject"] is None

    replay = student.post("/invitations/accept", json={"code": code}).json()
    assert replay["alreadyMember"] is True
    assert replay["membership"] == joined["membership"]
    assert invite["usedCount"] == 1
    second, _ = make_client("s_outside")
    exhausted = second.post("/invitations/accept", json={"code": code})
    assert exhausted.status_code == 409
    assert exhausted.json()["code"] == "INVITATION_EXHAUSTED"

    member_id = joined["membership"]["id"]
    state.memberships[member_id]["status"] = "removed"
    removed = student.post("/invitations/accept", json={"code": code})
    assert removed.status_code == 409
    assert removed.json()["code"] == "MEMBERSHIP_REAPPROVAL_REQUIRED"
    assert student.get("/auth/me").json()["nextAction"] == "join_class"

    invite["expiresAt"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    expired = student.post("/invitations/accept", json={"code": code})
    assert expired.status_code == 404
    assert expired.json()["code"] == "INVITATION_INVALID"
