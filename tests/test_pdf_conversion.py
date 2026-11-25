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


def _sample_pdf(page_count):
    sample = Path(__file__).resolve().parents[1] / "app/fixtures/sample.pdf"
    sample_page = PdfReader(sample).pages[0]
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_page(sample_page)
    with BytesIO() as output:
        writer.write(output)
        return output.getvalue()


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
    pdf = _sample_pdf(2)
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
            assert student.get(path + "/download").status_code == 404
            assert student.get("/ai-credentials").status_code == 403
            assert student.post(endpoint, headers=request_headers, json=job_body).status_code == 403

        first_tag = original.headers["etag"]
        patched = teacher.patch(path, headers={"If-Match": first_tag}, json={"content": {
            **draft["content"], "paragraphs": [{"id": "p1b1", "text": "교과 자료를 확인한다.", "page": 1}],
        }}).raise_for_status()
        assert teacher.post(path + "/review", headers={"If-Match": first_tag}).status_code == 412
        reviewed = teacher.post(path + "/review", headers={"If-Match": patched.headers["etag"]}).raise_for_status()
        assert teacher.post(path + "/publish", headers={"If-Match": reviewed.headers["etag"]}).status_code == 409
        assert teacher.get(path).raise_for_status().json()["status"] == "reviewed"
        assert teacher.post(path + "/publish", headers={"If-Match": reviewed.headers["etag"]},
                            files={"file": ("bad.pdf", b"invalid", "application/pdf")}).status_code == 415
        assert teacher.get(path).raise_for_status().json()["status"] == "reviewed"
        converted = _sample_pdf(1)
        published = teacher.post(path + "/publish", headers={"If-Match": reviewed.headers["etag"]},
                                 files={"file": ("converted.pdf", converted, "application/pdf")}).raise_for_status()
        assert published.json()["status"] == "published" and published.json()["hasPdf"]

    with TestClient(create_app() if database else teacher.app, base_url="http://testserver/api/v1") as student:
        student.post("/auth/login", json=student_login).raise_for_status()
        visible = student.get(path).raise_for_status().json()
        assert visible["content"]["paragraphs"][0]["text"] == "교과 자료를 확인한다."
        assert visible["content"]["limitedSupportPages"] == [1]
        assert visible["originalContent"] is None
        entries = student.get(school + "/materials").raise_for_status().json()["items"]
        assert any(entry["id"] == job["resultId"] and entry["hasPdf"] for entry in entries)
        download = student.get(path + "/download").raise_for_status()
        assert download.content == converted and download.headers["content-type"] == "application/pdf"
        assert student.get(f"/files/{source['fileId']}/download").status_code == 404

        student.post("/auth/login", json=student_login | {"loginId": "s_a"}).raise_for_status()
        assert student.get(path).status_code == 404
        assert student.get(path + "/download").status_code == 404


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
        if "context" in data:
            assert [block["id"] for block in data["blocks"]] == ["p2b2"]
            return {"blocks": [{"id": "p2b2", "audience": "teacher", "evidence": "지도 메모"}]}
        assert "분류" in instructions and "learner" not in data
        return {"blocks": [{"id": block["id"], "role": "body", "audience": "student"} for block in data["blocks"]]}

    result = convert_pdf(analyze, {"subject": "korean", "unit": "한글"},
                         {"level": "intermediate", "nativeLanguage": "vi"}, [page])
    assert len(requests) == 3
    assert result.limitedSupportPages == [2] and result.hiddenBlockIds == ["p2b2"]
    assert [row.id for row in result.paragraphs] == ["p2b1"]


def test_four_page_pdf_recovers_only_failed_pages_and_keeps_valid_support(monkeypatch):
    monkeypatch.delenv("GANADA_DB_HOST", raising=False)
    monkeypatch.delenv("GANADA_SOLAR_MODEL", raising=False)
    monkeypatch.setenv("GANADA_SOLAR_ENCRYPTION_KEY", Fernet.generate_key().decode())
    calls = []
    api_key = "not-a-live-key-four-pages"

    def respond(request):
        assert str(request.url) == solar.SOLAR_URL
        assert request.headers["Authorization"] == f"Bearer {api_key}"
        payload = json.loads(request.content)
        data = json.loads(payload["messages"][1]["content"])
        page = data["page"]
        is_primary = "learner" in data
        calls.append((page, "confirm" if "context" in data else "primary" if is_primary else "fallback"))
        rows = [
            {"id": block["id"], "role": "body", "audience": "student"}
            for block in data["blocks"]
        ]
        terms = {}
        if page == 1 and is_primary:
            content = '{"blocks":[{"id":"p1b1","audience":'
        else:
            if "context" in data:
                assert page == 2 and [row["id"] for row in rows] == ["p2b2"]
                rows[0].update(audience="teacher", evidence="지도 메모")
            elif page == 1:
                rows[1]["audience"] = "teacher"
            elif page == 2 and is_primary:
                rows[0].update(
                    hard=[{"from": "검증", "easy": "확인", "tier": "2-3"}],
                    visual={"kind": "invalid", "items": [1, 2]},
                )
                rows[1]["audience"] = "teacher"
                rows.pop()
            elif page == 3:
                rows[0]["terms"] = ["단위"]
                terms = {"단위": {"easy": "재는 데 쓰는 기준", "translation": "đơn vị"}}
            elif page == 4:
                rows[0]["visual"] = {"kind": "seq", "title": "비교 순서", "items": ["비교", "정리"]}
                rows[1]["hard"] = [{"from": "그림 안내", "easy": "문장이 바뀜", "tier": "1"}]
            content = json.dumps({"blocks": rows, "terms": terms}, ensure_ascii=False)

        return httpx.Response(200, json={"choices": [{
            "finish_reason": "stop", "message": {"content": content},
        }]})

    client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs)
    )
    job_pages = [
        {"number": 1, "blocks": [
            {"id": "p1b1", "text": "자료를 검토해 주세요."},
            {"id": "p1b2", "text": "교사용 참고: 검토자에게 전달할 내용입니다."},
        ]},
        {"number": 2, "blocks": [
            {"id": "p2b1", "text": "교과 자료를 검증한다."},
            {"id": "p2b2", "text": "문제와 함께 검토할 지도 메모입니다."},
            {"id": "p2b3", "text": "자료를 읽으세요."},
        ]},
        {"number": 3, "blocks": [{"id": "p3b1", "text": "이 자료의 단위를 확인합니다."}]},
        {"number": 4, "blocks": [
            {"id": "p4b1", "text": "서로 비교하고 결과를 정리합니다."},
            {"id": "p4b2", "text": "그림 안내", "preserve": True},
        ]},
    ]

    with TestClient(create_app(), base_url="http://testserver/api/v1") as teacher:
        login = teacher.post("/auth/login", json={
            "loginId": "t_math", "password": "mock-pass-123", "role": "teacher",
        }).raise_for_status()
        teacher.headers["X-CSRF-Token"] = login.json()["csrfToken"]
        teacher.post("/ai-credentials", json={"apiKey": api_key}).raise_for_status()
        source = teacher.post("/classes/cls_1/sources/pdf", data={"title": "4쪽 자료"}, files={
            "file": ("study.pdf", _sample_pdf(4), "application/pdf"),
        }).raise_for_status().json()
        request_body = {
            "sourceId": source["id"], "studentMemberIds": ["mem_s_b"], "operation": "convert",
            "pages": job_pages,
        }
        headers = {"Idempotency-Key": "four-page-recovery"}
        created = teacher.post("/classes/cls_1/generation-jobs", headers=headers, json=request_body)
        assert created.status_code == 202, created.text
        job_id = created.json()["items"][0]["id"]
        job = teacher.get(f"/generation-jobs/{job_id}").raise_for_status().json()
        assert job["status"] == "succeeded", job
        material = teacher.get(f"/materials/{job['resultId']}").raise_for_status().json()
        assert material["status"] == "draft"
        content = material["content"]

        replay = teacher.post("/classes/cls_1/generation-jobs", headers=headers, json=request_body)
        assert replay.raise_for_status().json()["items"][0]["id"] == job_id

    assert calls == [
        (1, "primary"), (1, "fallback"), (2, "primary"), (2, "fallback"), (2, "confirm"),
        (3, "primary"), (4, "primary"),
    ]
    assert content["limitedSupportPages"] == [1, 2]
    assert content["omittedPages"] == []
    assert content["hiddenBlockIds"] == ["p1b2", "p2b2"]
    assert [(row["id"], row["page"], row["text"]) for row in content["paragraphs"]] == [
        ("p1b1", 1, "자료를 검토해 주세요."),
        ("p2b1", 2, "교과 자료를 확인한다."),
        ("p2b3", 2, "자료를 읽으세요."),
        ("p3b1", 3, "이 자료의 단위를 확인합니다."),
        ("p4b1", 4, "서로 비교하고 결과를 정리합니다."),
        ("p4b2", 4, "그림 안내"),
    ]
    assert content["translations"] == [
        {"term": "단위", "meaning": "재는 데 쓰는 기준", "translation": "đơn vị", "language": "vi"},
    ]
    assert [note["blockId"] for note in content["studyNotes"]] == ["p4b1"]
    assert content["studyNotes"][0]["visual"]["items"] == ["비교", "정리"]


@pytest.mark.parametrize("code", [
    "SOLAR_OUTPUT_TRUNCATED", "SOLAR_OUTPUT_JSON_INVALID", "SOLAR_OUTPUT_EMPTY", "SOLAR_RESPONSE_INVALID",
])
def test_pdf_retries_bad_completion_once_and_reports_page_if_recovery_fails(code):
    page = PdfPage(number=3, blocks=[PdfBlock(id="p3b1", text="세 번째 쪽입니다.")])
    calls = []

    def analyze(_instructions, data):
        calls.append(data["page"])
        raise APIError(502, code, solar.ERROR_MESSAGES[code])

    with pytest.raises(APIError) as error:
        convert_pdf(analyze, {"subject": "math", "unit": ""},
                    {"level": "intermediate", "nativeLanguage": "vi"}, [page])
    assert calls == [3, 3]
    assert error.value.code == code
    assert error.value.details["page"] == 3


@pytest.mark.parametrize("code", ["SOLAR_READ_TIMEOUT", "SOLAR_CONNECT_TIMEOUT", "SOLAR_OUTPUT_FILTERED"])
def test_pdf_does_not_retry_timeout_or_content_filter(code):
    page = PdfPage(number=3, blocks=[PdfBlock(id="p3b1", text="세 번째 쪽입니다.")])
    calls = []

    def analyze(_instructions, data):
        calls.append(data["page"])
        raise APIError(502, code, solar.ERROR_MESSAGES[code])

    with pytest.raises(APIError) as error:
        convert_pdf(analyze, {"subject": "math", "unit": ""},
                    {"level": "intermediate", "nativeLanguage": "vi"}, [page])
    assert calls == [3]
    assert error.value.code == code
    assert error.value.details["page"] == 3


@pytest.mark.parametrize(("glossary", "translation", "limited"), [
    pytest.param([{"term": "비율", "meaning": "두 양을 비교한 값", "translation": "tỉ lệ"}], "tỉ lệ", [],
                 id="explicit-term-and-meaning-alias"),
    pytest.param({"비율": "두 양을 비교한 값"}, "", [], id="keyed-string-definition"),
    pytest.param({"비율": {"easy": "두 양을 비교한 값", "translation": ["tỉ lệ"]}}, "", [1],
                 id="keep-definition-with-invalid-translation"),
])
def test_pdf_accepts_keyed_blocks_and_glossary_shorthand(monkeypatch, glossary, translation, limited):
    completion = {
        "blocks": {
            "p1b2": {"role": "body", "audience": "student"},
            "p1b1": {"role": "body", "audience": "student", "terms": "비율",
                     "hard": [{"from": "검증", "easy": "확인", "tier": "2-3"}]},
        },
        "terms": glossary,
    }
    requests = _mock_upstage(monkeypatch, content=json.dumps(completion, ensure_ascii=False))
    page = PdfPage(number=1, blocks=[
        PdfBlock(id="p1b1", text="비율을 검증합니다."),
        PdfBlock(id="p1b2", text="두 수를 비교합니다."),
    ])

    def analyze(instructions, data):
        return solar.complete_json("not-a-live-key", solar.DEFAULT_MODEL, instructions, data, for_pdf=True)

    result = convert_pdf(analyze, {"subject": "math", "unit": "비와 비율"},
                         {"level": "beginner", "nativeLanguage": "vi"}, [page])

    assert len(requests) == 1
    assert result.limitedSupportPages == limited
    assert [(row.id, row.text) for row in result.paragraphs] == [
        ("p1b1", "비율을 확인합니다."),
        ("p1b2", "두 수를 비교합니다."),
    ]
    assert result.translations == [
        {"term": "비율", "meaning": "두 양을 비교한 값", "translation": translation, "language": "vi"},
    ]


def test_pdf_key_and_internal_id_conflict_only_reuses_unambiguous_rows():
    page = PdfPage(number=1, blocks=[
        PdfBlock(id="p1b1", text="비율을 검증합니다."),
        PdfBlock(id="p1b2", text="다른 값도 검증합니다."),
    ])
    requests = []

    def analyze(instructions, request):
        requests.append(request)
        if len(requests) == 1:
            return {
                "blocks": {
                    "p1b1": {"id": "p1b2", "role": "body", "audience": "student", "terms": "비율",
                             "hard": [{"from": "검증", "easy": "확인", "tier": "2-3"}]},
                    "p1b2": {"role": "body", "audience": "student",
                             "hard": [{"from": "다른 값", "easy": "다른 수", "tier": "2-3"}]},
                },
                "terms": {"비율": "두 양을 비교한 값"},
            }
        assert "분류" in instructions
        assert set(request) == {"page", "subject", "unit", "blocks"}
        return {"blocks": [
            {"id": block["id"], "role": "body", "audience": "student"} for block in request["blocks"]
        ]}

    result = convert_pdf(analyze, {"subject": "math", "unit": "비와 비율"},
                         {"level": "beginner", "nativeLanguage": "vi"}, [page])

    assert len(requests) == 2 and result.limitedSupportPages == [1]
    assert [row.text for row in result.paragraphs] == ["비율을 검증합니다.", "다른 수도 검증합니다."]
    assert not result.translations


def test_pdf_tracks_only_contextual_phrase_changes_and_preserves_quoted_text():
    original = "📘 소리를 내는 발음 기관을 관찰합니다. ‘관찰합니다’는 인용 표현입니다."
    page = PdfPage(number=1, blocks=[PdfBlock(id="p1b1", text=original)])

    def analyze(_instructions, _request):
        return {"blocks": [{
            "id": "p1b1", "role": "body", "audience": "student",
            "hard": [
                {"from": "발음 기관", "easy": "소리를 내는 몸의 부분", "note": "몸의 부분", "tier": "3"},
                {"from": "관찰합니다", "easy": "살펴봅니다", "tier": "3"},
                {"from": "인용 표현", "easy": "인용 표현.", "tier": "3"},
            ],
        }]}

    result = convert_pdf(analyze, {"subject": "korean", "unit": "한글"},
                         {"level": "advanced", "nativeLanguage": "zh"}, [page])
    paragraph, = result.paragraphs
    assert paragraph.text == "📘 소리를 내는 발음 기관(몸의 부분)을 살펴봅니다. ‘관찰합니다’는 인용 표현입니다."
    assert [change.original for change in paragraph.phraseChanges] == ["발음 기관", "관찰합니다"]
    for change in paragraph.phraseChanges:
        assert paragraph.text[change.start:change.start + len(change.replacement)] == change.replacement


def test_pdf_protects_layout_and_confirms_only_plausible_teacher_blocks():
    page = PdfPage(number=4, blocks=[
        PdfBlock(id="p4b1", text="114"),
        PdfBlock(id="p4b2", text="그림 2 자음 기관"),
        PdfBlock(id="p4b3", text="표 1 소리의 특징"),
        PdfBlock(id="p4b4", text="어떤 차이가 있는지 설명해 보세요."),
        PdfBlock(id="p4b5", text="말소리의 원리를 검증합니다.", layout="text"),
        PdfBlock(id="p4b6", text="교사용 참고: 발음 예시", layout="margin"),
        PdfBlock(id="p4b7", text="화살표", layout="image", preserve=True),
        PdfBlock(id="p4b8", text="활동 과정에서 찾은 규칙을 검증합니다.", layout="text"),
        PdfBlock(id="p4b9", text="① 예시 답안 문장입니다.", layout="text"),
        PdfBlock(id="p4b10", text="표 2 예시 답안 기록", layout="structure"),
        PdfBlock(id="p4b11", text="② 서로 닮은 소리", layout="text"),
        PdfBlock(id="p4b12", text="빈칸에 알맞은 문장을 써 보세요.", layout="text"),
    ])
    requests = []

    def analyze(instructions, request):
        requests.append(request)
        if "learner" not in request:
            assert "실제로 교사에게만" in instructions
            assert [row["id"] for row in request["blocks"]] == ["p4b5", "p4b9", "p4b10"]
            assert all("audience" not in row for row in request["context"])
            return {"blocks": [
                {"id": "p4b5", "audience": "student"},
                {"id": "p4b9", "audience": "teacher", "evidence": "예시 답안"},
                {"id": "p4b10", "audience": "teacher", "evidence": "예시 답안"},
            ]}

        preserve = {row["id"] for row in request["blocks"] if row["preserve"]}
        assert {"p4b1", "p4b2", "p4b3", "p4b7"} <= preserve
        assert "p4b8" not in preserve and "p4b12" not in preserve
        assert request["blocks"][6]["layout"] == "image"
        return {"blocks": [
            {"id": block.id, "role": "body",
             "audience": "student" if block.id in {"p4b6", "p4b8", "p4b11", "p4b12"} else "teacher",
             "hard": [
                 {"from": "검증", "easy": "확인", "tier": "2-3"},
                 {"from": "닮은", "easy": "비슷한", "tier": "2-3"},
                 {"from": "알맞은", "easy": "맞는", "tier": "2-3"},
             ]}
            for block in page.blocks
        ]}

    result = convert_pdf(analyze, {"subject": "korean", "unit": "한글"},
                         {"level": "beginner", "nativeLanguage": "vi"}, [page])

    assert len(requests) == 2
    assert result.hiddenBlockIds == ["p4b6", "p4b9", "p4b10"]
    assert result.limitedSupportPages == [4]
    text_by_id = {row.id: row.text for row in result.paragraphs}
    assert text_by_id == {
        "p4b1": "114", "p4b2": "그림 2 자음 기관", "p4b3": "표 1 소리의 특징",
        "p4b4": "어떤 차이가 있는지 설명해 보세요.", "p4b5": "말소리의 원리를 검증합니다.",
        "p4b7": "화살표", "p4b8": "활동 과정에서 찾은 규칙을 확인합니다.",
        "p4b11": "② 서로 닮은 소리", "p4b12": "빈칸에 맞는 문장을 써 보세요.",
    }


@pytest.mark.parametrize(("audience", "evidence"), [
    ("uncertain", None),
    ("teacher", None),
    ("teacher", "나타냅니다"),
    ("teacher", "실제로 적혀 있지 않은 교사용 정답"),
])
def test_pdf_refuses_uncertain_teacher_confirmation(audience, evidence):
    page = PdfPage(number=2, blocks=[
        PdfBlock(id="p2b1", text="말소리의 원리를 나타냅니다.", layout="text"),
    ])
    requests = []

    def analyze(_instructions, data):
        requests.append(data)
        if "learner" in data:
            return {"blocks": [{"id": "p2b1", "role": "body", "audience": "teacher"}]}
        return {"blocks": [{"id": "p2b1", "audience": audience, "evidence": evidence}]}

    with pytest.raises(APIError) as error:
        convert_pdf(analyze, {"subject": "korean", "unit": "한글"},
                    {"level": "beginner", "nativeLanguage": "vi"}, [page])
    assert len(requests) == 2
    assert error.value.code == "SOLAR_AUDIENCE_UNCERTAIN"
    assert error.value.details["page"] == 2


def test_pdf_explicit_teacher_label_in_protected_geometry_fails_closed():
    page = PdfPage(number=2, blocks=[
        PdfBlock(id="p2b1", text="교사용 정답: 가나다", layout="image", preserve=True),
    ])

    def analyze(_instructions, _data):
        return {"blocks": [{"id": "p2b1", "role": "caption", "audience": "student"}]}

    with pytest.raises(APIError) as error:
        convert_pdf(analyze, {"subject": "korean", "unit": "한글"},
                    {"level": "beginner", "nativeLanguage": "vi"}, [page])
    assert error.value.code == "SOLAR_AUDIENCE_UNCERTAIN"
    assert error.value.details["page"] == 2
