"""Regressions for uploaded bytes and the material review lifecycle."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.materials.dto import Content
from app.materials.service import create_draft


@pytest.fixture
def sessions():
    app = create_app()
    clients = []

    def login(login_id):
        client = TestClient(app, base_url="http://testserver/api/v1")
        clients.append(client)
        response = client.post(
            "/auth/login",
            json={
                "loginId": login_id,
                "password": "mock-pass-123",
                "role": "teacher" if login_id.startswith("t_") else "student",
            },
        )
        assert response.status_code == 200, response.text
        client.headers["X-CSRF-Token"] = response.json()["csrfToken"]
        return client

    yield app.state.store, login
    for client in clients:
        client.close()


def test_uploaded_pdf_is_extracted_and_authorized_download_returns_original_bytes(sessions):
    state, login = sessions
    math = login("t_math")
    science = login("t_science")
    student = login("s_a")
    upload_path = "/classes/cls_1/files"
    sources_path = "/classes/cls_1/sources"

    example = math.get("/files/file_src_math_1/download")
    assert example.status_code == 200, example.text
    assert example.headers["Content-Type"] == "application/pdf"
    pdf = example.content

    fake = math.post(
        upload_path, files={"file": ("broken.pdf", b"%PDF-1.4\n", "application/pdf")}
    )
    assert fake.status_code == 422
    assert fake.json()["code"] == "PDF_UNREADABLE"

    uploaded = math.post(
        upload_path, files={"file": ("수업 자료.pdf", pdf, "application/pdf")}
    )
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["extractionStatus"] == "ready"
    file_id = uploaded.json()["id"]
    assert "data" not in uploaded.json()
    download_path = f"/files/{file_id}/download"
    assert math.get(download_path).status_code == 404

    foreign_registration = science.post(sources_path, json={"fileId": file_id, "title": "외부"})
    assert foreign_registration.status_code == 404
    source = math.post(
        sources_path, json={"fileId": file_id, "title": "분수 연습", "unit": "1단원"}
    )
    assert source.status_code == 201, source.text
    item = source.json()
    assert item["sourceText"] == "Synthetic mock PDF - not uploaded content"
    assert item["sentences"] and item["sentences"][0]["text"] in item["sourceText"]
    assert item["subject"] == "math"

    downloaded = math.get(download_path)
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == pdf
    assert downloaded.headers["Content-Disposition"].startswith("attachment;")
    assert science.get(download_path).status_code == 404
    assert student.get(download_path).status_code == 404

    original = "/files/file_src_math_1/download"
    assert student.get(original).status_code == 200
    state.quizzes["quiz_a_math_1"]["inputMaterialId"] = "mat_a_math_1"
    assert student.get(original).status_code == 404

    state.sources[item["id"]]["archived"] = True
    assert math.get(download_path).status_code == 404


def test_material_draft_revision_and_review_edit_publish_gates(sessions):
    state, login = sessions
    teacher = login("t_math")
    other_teacher = login("t_science")
    assigned = login("s_b")
    other_student = login("s_a")
    content = Content(paragraphs=[{"id": "s1", "text": "학생별 학습 자료입니다."}])
    draft = create_draft(state, state.users["usr_t_math"], "src_math_1", "mem_s_b", content)
    assert draft["revision"] == 2
    assert draft["diagnosisSnapshot"]["nativeLanguage"] == "vi"
    path = f"/materials/{draft['id']}"

    opened = teacher.get(path)
    assert opened.status_code == 200
    assert opened.json()["originalContent"]
    assert "diagnosisSnapshot" not in opened.json()
    assert other_teacher.get(path).status_code == 404
    assert assigned.get(path).status_code == 404
    assert teacher.post(path + "/review").status_code == 428

    version = {"If-Match": opened.headers["ETag"]}
    reviewed = teacher.post(path + "/review", headers=version)
    assert reviewed.status_code == 200, reviewed.text
    assert teacher.post(path + "/publish", headers=version).status_code == 412

    edited = teacher.patch(
        path,
        headers={"If-Match": reviewed.headers["ETag"]},
        json={"content": content.model_dump()},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["status"] == "draft"
    assert edited.json()["reviewedAt"] is None
    assert teacher.post(
        path + "/publish", headers={"If-Match": edited.headers["ETag"]}
    ).status_code == 409

    reviewed = teacher.post(path + "/review", headers={"If-Match": edited.headers["ETag"]})
    assert reviewed.status_code == 200, reviewed.text
    published = teacher.post(
        path + "/publish", headers={"If-Match": reviewed.headers["ETag"]}
    )
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published"
    assert published.json()["isRecentPublished"]
    assert other_student.get(path).status_code == 404

    student_view = assigned.get(path)
    assert student_view.status_code == 200, student_view.text
    assert student_view.json()["content"] == content.model_dump()
    assert student_view.json()["originalContent"] is None
    assert teacher.patch(
        path, headers={"If-Match": published.headers["ETag"]}, json={"content": content.model_dump()}
    ).status_code == 409
