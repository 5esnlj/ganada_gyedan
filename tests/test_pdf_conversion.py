"""Exercise teacher PDF generation without contacting Upstage."""

from io import BytesIO
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import httpx
from pypdf import PdfReader, PdfWriter
import pytest

from app.core.errors import APIError
from app.generation import solar
from app.generation.dto import PdfBlock, PdfPage
from app.generation.prompts import convert_pdf
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
    monkeypatch.delenv("GANADA_SOLAR_MODEL", raising=False)
    monkeypatch.setenv("GANADA_SOLAR_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("GANADA_DB_FILES_DIR", str(tmp_path))
    calls = []
    secret = "not-a-live-key-abcd"

    def fake_solar(api_key, model, instructions, data, *, for_pdf):
        assert api_key == secret and model == "solar-pro4" and for_pdf
        assert "수식" in instructions
        calls.append(data)
        return {
            "blocks": [
                {"id": "p1b1", "role": "body", "audience": "student",
                 "hard": [{"from": "검증", "easy": "확인", "note": "살펴보는 일", "tier": "2-3"}],
                 "visual": {"kind": "list", "items": [1, 2]}},
                {"id": "p1b2", "role": "body", "audience": "teacher"},
            ],
            "terms": {"교과": {"easy": "학교에서 배우는 과목", "translation": "学科"}},
        }

    monkeypatch.setattr(solar, "complete_json", fake_solar)
    sample = Path(__file__).resolve().parents[1] / "app/fixtures/sample.pdf"
    sample_page = PdfReader(sample).pages[0]
    upload_pdf = PdfWriter()
    upload_pdf.add_page(sample_page)
    upload_pdf.add_page(sample_page)
    binary = BytesIO()
    upload_pdf.write(binary)
    pdf = binary.getvalue()
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
        assert teacher.get(f"/sources/{source['id']}/pdf-info").raise_for_status().json()["pageCount"] == 2

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

        def timeout_second_page(_key, _model, _instructions, data, *, for_pdf):
            assert data["page"] == 2 and for_pdf
            raise APIError(504, "SOLAR_READ_TIMEOUT", solar.ERROR_MESSAGES["SOLAR_READ_TIMEOUT"])

        monkeypatch.setattr(solar, "complete_json", timeout_second_page)
        failing_body = {**job_body, "pages": [{"number": 2, "blocks": [{"id": "p2b1", "text": "두 번째 쪽"}]}]}
        queued = teacher.post(endpoint, headers={"Idempotency-Key": "failing-page"}, json=failing_body)
        failed_id = queued.raise_for_status().json()["items"][0]["id"]
        failed = teacher.get(f"/generation-jobs/{failed_id}").raise_for_status().json()
        assert failed["status"] == "unknown" and failed["resultId"] is None
        assert failed["errorCode"] == "SOLAR_READ_TIMEOUT"
        assert failed["errorMessage"].startswith("2쪽 · ")

        path = f"/materials/{job['resultId']}"
        original = teacher.get(path).raise_for_status()
        draft = original.json()
        assert draft["status"] == "draft" and draft["content"]["hiddenBlockIds"] == ["p1b2"]
        assert draft["content"]["limitedSupportPages"] == [1]
        assert draft["content"]["paragraphs"][0]["text"] == "교과 자료를 확인한다."
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
        assert visible["content"]["limitedSupportPages"] == [1]
        assert visible["originalContent"] is None


def _mock_upstage(monkeypatch, *, finish_reason="stop", content=None, error_type=None):
    requests = []
    client = httpx.Client

    def respond(request):
        requests.append(request)
        if error_type is not None:
            raise error_type("test timeout", request=request)
        return httpx.Response(200, json={"choices": [{
            "finish_reason": finish_reason, "message": {"content": content},
        }]})

    def client_factory(**kwargs):
        return client(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(httpx, "Client", client_factory)
    return requests


def test_solar_wire_contract_without_network(monkeypatch):
    monkeypatch.setenv("GANADA_SOLAR_MODEL", "solar-pro-4")
    assert solar.model_name() == "solar-pro4"
    text = '분석 결과입니다.\n```json\n{"blocks":[],"terms":{}}\n```'
    requests = _mock_upstage(monkeypatch, finish_reason="stop", content=text)

    # Queued jobs from the previous version may still have the old model ID.
    result = solar.complete_json("not-a-live-key", "solar-pro-4", "변환 지시", {"blocks": []})
    assert result == {"blocks": [], "terms": {}}
    request, = requests
    assert str(request.url) == solar.SOLAR_URL
    assert request.headers["Authorization"] == "Bearer not-a-live-key"
    body = json.loads(request.content)
    assert (body["model"], body["reasoning_effort"], body["max_tokens"]) == ("solar-pro4", "low", 16_384)
    assert body["messages"][0]["role"] == "system"
    assert json.loads(body["messages"][1]["content"]) == {"blocks": []}


@pytest.mark.parametrize(("reason", "content", "code"), [
    ("length", "", "SOLAR_OUTPUT_TRUNCATED"),
    ("content_filter", None, "SOLAR_OUTPUT_FILTERED"),
    ("stop", None, "SOLAR_OUTPUT_EMPTY"),
    ("stop", '{"blocks": [', "SOLAR_OUTPUT_JSON_INVALID"),
])
def test_solar_distinguishes_completion_failures(monkeypatch, reason, content, code):
    _mock_upstage(monkeypatch, finish_reason=reason, content=content)
    with pytest.raises(APIError) as error:
        solar.complete_json("not-a-live-key", solar.DEFAULT_MODEL, "변환 지시", {})
    assert error.value.code == code


@pytest.mark.parametrize(("exception_type", "code"), [
    (httpx.ConnectTimeout, "SOLAR_CONNECT_TIMEOUT"),
    (httpx.ReadTimeout, "SOLAR_READ_TIMEOUT"),
])
def test_pdf_solar_timeout_is_classified_without_resending(monkeypatch, caplog, exception_type, code):
    requests = _mock_upstage(monkeypatch, error_type=exception_type)
    with pytest.raises(APIError) as error:
        solar.complete_json("not-a-live-key", solar.DEFAULT_MODEL, "PDF 변환 지시", {"page": 2}, for_pdf=True)
    assert error.value.code == code
    request, = requests
    assert request.extensions["timeout"]["connect"] == 8.0
    assert request.extensions["timeout"]["read"] == 180.0
    assert json.loads(request.content)["reasoning_effort"] == "none"
    assert f"page=2, phase={exception_type.__name__}" in caplog.text
    assert "not-a-live-key" not in caplog.text


@pytest.mark.parametrize(("row_ids", "audience", "expected_code"), [
    (["p2b2", "p2b1"], "student", None),
    (["p2b1", "p2b1"], "student", "SOLAR_OUTPUT_BLOCKS"),
    (["p2b1", "p2b2"], "unknown", "SOLAR_OUTPUT_SCHEMA"),
])
def test_solar_pdf_keeps_source_order_and_validates_classification(row_ids, audience, expected_code):
    calls = []

    def analyze(_instructions, _request):
        calls.append(_request["page"])
        return {"blocks": [{"id": row_id, "role": "body", "audience": audience} for row_id in row_ids]}

    page = PdfPage(number=2, blocks=[
        PdfBlock(id="p2b1", text="첫 문단입니다."), PdfBlock(id="p2b2", text="두 번째 문단입니다."),
    ])
    source = {"subject": "math", "unit": "비와 비율"}
    learner = {"level": "intermediate", "nativeLanguage": "vi"}
    if expected_code is None:
        result = convert_pdf(analyze, source, learner, [page])
        assert [row.id for row in result.paragraphs] == ["p2b1", "p2b2"]
        assert calls == [2]
    else:
        with pytest.raises(APIError) as error:
            convert_pdf(analyze, source, learner, [page])
        assert error.value.code == expected_code
        assert error.value.details == {"page": 2}
        assert calls == [2, 2]


def test_pdf_keeps_usable_annotations_when_one_page_has_bad_optional_fields():
    page = PdfPage(number=2, blocks=[
        PdfBlock(id="p2b1", text="자음자와 모양을 본떠 나타냅니다."),
        PdfBlock(id="p2b2", text="ㄱ ㅋ ㄲ", preserve=True),
        PdfBlock(id="p2b3", text="교사용 정답: 글자의 모양"),
    ])

    def analyze(_instructions, request):
        assert request["page"] == 2 and len(request["blocks"]) == 3
        return {
            "blocks": [
                {"id": "p2b1", "role": "body", "audience": "student", "joins": None,
                 "terms": ["자음자", "모양"],
                 "hard": [{"from": "본떠", "easy": "보고 따라", "note": None, "tier": 3},
                          {"from": "나타냅니다", "easy": ["보입니다"], "tier": "3"}],
                 "visual": {"kind": "list", "items": ["ㄱ", "ㄴ", "ㅁ", "ㅅ", "ㅇ", "ㅋ", "ㄲ"]}},
                {"id": "p2b2", "role": "other", "audience": "student", "terms": None},
                {"id": "p2b3", "role": "body", "audience": "teacher", "hard": None},
            ],
            "terms": {"자음자": {"easy": "자음을 쓰는 글자", "translation": None}, "모양": {"easy": False}},
        }

    result = convert_pdf(analyze, {"subject": "korean", "unit": "한글"},
                         {"level": "beginner", "nativeLanguage": "zh"}, [page])
    assert [(row.id, row.text) for row in result.paragraphs] == [
        ("p2b1", "자음자와 모양을 보고 따라 나타냅니다."), ("p2b2", "ㄱ ㅋ ㄲ"),
    ]
    assert result.hiddenBlockIds == ["p2b3"]
    assert result.limitedSupportPages == [2] and result.omittedPages == []
    assert result.translations == [{"term": "자음자", "meaning": "자음을 쓰는 글자", "translation": "", "language": "zh"}]
    assert not result.studyNotes


def test_pdf_recovers_missing_classification_without_revealing_known_teacher_content():
    page = PdfPage(number=2, blocks=[
        PdfBlock(id="p2b1", text="학생이 읽을 본문입니다."),
        PdfBlock(id="p2b2", text="지도 메모 내용입니다."),
    ])
    requests = []

    def analyze(instructions, data):
        requests.append(data)
        if len(requests) == 1:
            return {"blocks": [{"id": "p2b2", "role": "body", "audience": " 교사용 "}]}
        assert "분류" in instructions and "learner" not in data
        return {"blocks": [{"id": block["id"], "role": "body", "audience": "student"} for block in data["blocks"]]}

    result = convert_pdf(analyze, {"subject": "korean", "unit": "한글"},
                         {"level": "intermediate", "nativeLanguage": "vi"}, [page])
    assert len(requests) == 2
    assert result.limitedSupportPages == [2] and result.hiddenBlockIds == ["p2b2"]
    assert [row.id for row in result.paragraphs] == ["p2b1"]
