"""Exercise teacher PDF generation without contacting Upstage."""

from pathlib import Path
import os

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import pytest

from app.generation import solar
from app.main import create_app


@pytest.mark.parametrize("database", [
    False,
    pytest.param(True, marks=pytest.mark.skipif(
        os.getenv("GANADA_TEST_MARIADB") != "1", reason="Requires disposable seeded MariaDB",
    )),
])
def test_pdf_conversion_review_and_publishing(database, monkeypatch, tmp_path):
    if not database:
        monkeypatch.delenv("GANADA_DB_HOST", raising=False)
    monkeypatch.setenv("GANADA_SOLAR_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("GANADA_DB_FILES_DIR", str(tmp_path))
    calls = []
    secret = "not-a-live-key-abcd"

    def fake_solar(api_key, model, instructions, data):
        assert api_key == secret and model == "solar-pro-4"
        assert "수식" in instructions
        calls.append(data)
        return {
            "blocks": [
                {"id": "p1b1", "role": "body", "audience": "student",
                 "hard": [{"from": "검증", "easy": "확인", "note": "살펴보는 일", "tier": "2-3"}]},
                {"id": "p1b2", "role": "body", "audience": "teacher"},
            ],
            "terms": {"교과": {"easy": "학교에서 배우는 과목", "translation": "学科"}},
        }

    monkeypatch.setattr(solar, "complete_json", fake_solar)
    pdf = (Path(__file__).resolve().parents[1] / "app/fixtures/sample.pdf").read_bytes()
    student_login = {"loginId": "s_b", "password": "mock-pass-123", "role": "student"}
    request_headers = {"Idempotency-Key": "same-generation"}

    with TestClient(create_app(), base_url="http://testserver/api/v1") as teacher:
        csrf = teacher.post("/auth/login", json={
            "loginId": "t_math", "password": "mock-pass-123", "role": "teacher",
        }).raise_for_status().json()["csrfToken"]
        teacher.headers["X-CSRF-Token"] = csrf
        class_id = teacher.get("/classes").raise_for_status().json()["items"][0]["id"]
        school = f"/classes/{class_id}"
        students = teacher.get(school + "/students").raise_for_status().json()["items"]
        student_id = next(s["id"] for s in students if s["displayName"] == "학생 B (가상)")
        response = teacher.post("/ai-credentials", json={"apiKey": secret}).raise_for_status()
        assert secret not in response.text and response.json()["maskedSuffix"] == "abcd"
        source = teacher.post(school + "/sources/pdf", data={"title": "변환 테스트"}, files={
            "file": ("source.pdf", pdf, "application/pdf"),
        }).raise_for_status().json()
        assert teacher.get(f"/files/{source['fileId']}/download").raise_for_status().content == pdf
        assert teacher.get(f"/sources/{source['id']}/pdf-info").raise_for_status().json()["pageCount"] == 1

        job_body = {
            "sourceId": source["id"], "studentMemberIds": [student_id], "operation": "convert",
            "pages": [{"number": 1, "blocks": [
                {"id": "p1b1", "text": "교과 자료를 검증한다.", "preserve": False},
                {"id": "p1b2", "text": "교사용 정답", "preserve": False},
            ]}],
        }
        endpoint = school + "/generation-jobs"
        created = teacher.post(endpoint, headers=request_headers, json=job_body)
        assert created.status_code == 202, created.text
        job_id = created.json()["items"][0]["id"]
        job = teacher.get(f"/generation-jobs/{job_id}").raise_for_status().json()
        assert job["status"] == "succeeded", job
        assert "snapshot" not in job and "교사용 정답" not in str(job)
        assert len(calls) == 1 and calls[0]["learner"]["nativeLanguage"] == "vi"
        assert "displayName" not in calls[0]["learner"]

        replay = teacher.post(endpoint, headers=request_headers, json=job_body)
        assert replay.raise_for_status().json()["items"][0]["id"] == job_id
        changed = {**job_body, "pages": [{"number": 1, "blocks": [{"id": "p1b1", "text": "다른 자료"}]}]}
        assert teacher.post(endpoint, headers=request_headers, json=changed).status_code == 409
        assert len(calls) == 1

        path = f"/materials/{job['resultId']}"
        original = teacher.get(path).raise_for_status()
        draft = original.json()
        assert draft["status"] == "draft" and draft["content"]["hiddenBlockIds"] == ["p1b2"]
        assert all("정답" not in p["text"] for p in draft["content"]["paragraphs"])

        with TestClient(create_app(), base_url="http://testserver/api/v1") if database else TestClient(
            teacher.app, base_url="http://testserver/api/v1",
        ) as student:
            token = student.post("/auth/login", json=student_login).raise_for_status().json()["csrfToken"]
            student.headers["X-CSRF-Token"] = token
            assert student.get(path).status_code == 404
            assert student.get("/ai-credentials").status_code == 403
            assert student.post(endpoint, headers=request_headers, json=job_body).status_code == 403

        first_tag = original.headers["etag"]
        patched = teacher.patch(path, headers={"If-Match": first_tag}, json={"content": {
            **draft["content"], "paragraphs": [{"id": "p1b1", "text": "교과 자료를 확인한다.", "page": 1}],
        }}).raise_for_status()
        assert teacher.post(path + "/review", headers={"If-Match": first_tag}).status_code == 412
        reviewed = teacher.post(path + "/review", headers={"If-Match": patched.headers["etag"]}).raise_for_status()
        published = teacher.post(path + "/publish", headers={"If-Match": reviewed.headers["etag"]}).raise_for_status()
        assert published.json()["status"] == "published"

    with TestClient(create_app() if database else teacher.app, base_url="http://testserver/api/v1") as student:
        student.post("/auth/login", json=student_login).raise_for_status()
        visible = student.get(path).raise_for_status().json()
        assert visible["content"]["paragraphs"][0]["text"] == "교과 자료를 확인한다."
        assert visible["originalContent"] is None


def test_solar_wire_contract_without_network(monkeypatch):
    import json
    import httpx

    class ClientFactory:
        def __init__(self):
            self.actual_client = httpx.Client
            self.seen = False

        def respond(self, request):
            assert str(request.url) == solar.SOLAR_URL
            assert request.headers["Authorization"] == "Bearer not-a-live-key"
            body = json.loads(request.content)
            assert body["model"] == "solar-pro-4"
            assert body["messages"][0]["role"] == "system"
            assert json.loads(body["messages"][1]["content"]) == {"blocks": []}
            self.seen = True
            return httpx.Response(200, json={"choices": [{
                "finish_reason": "stop", "message": {"content": '{"blocks":[],"terms":{}}'},
            }]})

        def __call__(self, **kwargs):
            return self.actual_client(transport=httpx.MockTransport(self.respond), **kwargs)

    factory = ClientFactory()
    monkeypatch.setattr(httpx, "Client", factory)
    assert solar.complete_json("not-a-live-key", solar.DEFAULT_MODEL, "변환 지시", {"blocks": []}) == {
        "blocks": [], "terms": {},
    }
    assert factory.seen
