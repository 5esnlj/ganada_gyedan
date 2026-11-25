"""Completion envelope recovery is limited to the PDF workflow."""

import json

import httpx
import pytest

from app.core.errors import APIError
from app.generation import solar

ANSWER = {"blocks": [{"id": "p2b1", "audience": "student", "terms": []}], "terms": {}}
ANSWER_JSON = json.dumps(ANSWER)


def completion(content, *, reason="stop", **message_fields):
    return httpx.Response(200, json={"choices": [{
        "finish_reason": reason,
        "message": {"content": content, **message_fields},
    }]})


@pytest.mark.parametrize("content", [
    f" \n\ufeff {ANSWER_JSON}\t",
    f"결과입니다.\n{ANSWER_JSON}\n변환 내역입니다.",
    f"아래 내용을 확인해 주세요.\r\n```JSON\r\n{ANSWER_JSON}\r\n```\r\n완료했습니다.",
    f"```\n{ANSWER_JSON}\n```",
    f"[{ANSWER_JSON}]",
    json.dumps(ANSWER_JSON),
    [{"type": "text", "text": ANSWER_JSON[:20]}, {"type": "text", "text": ANSWER_JSON[20:]}],
    ANSWER_JSON.replace('"terms": []}', '"terms": [],}').replace('}],', '},],'),
])
def test_pdf_completion_decodes_one_complete_object(content):
    assert solar._completion_object(completion(content), for_pdf=True, page=2) == ANSWER


def test_pdf_trailing_comma_recovery_preserves_commas_and_braces_inside_strings():
    text = '표시: ,} 와 ,] 및 "인용"'
    content = '{"text":' + json.dumps(text, ensure_ascii=False) + ',}'
    assert solar._completion_object(completion(content), for_pdf=True) == {"text": text}


@pytest.mark.parametrize("content", [
    "요청한 형식으로 답할 수 없습니다.",
    '{"blocks":[{"id":"p2b1","audience":"student"}]',
    '{"blocks":[{"id":"p2b1","audience":"student"},]',
    f"{ANSWER_JSON}\n추가 결과\n{ANSWER_JSON}",
    f"{ANSWER_JSON}[]",
    f"[{ANSWER_JSON}, {ANSWER_JSON}]",
    f"```json\n{ANSWER_JSON}\n```\n```json\n{ANSWER_JSON}\n```",
    f"```json\n{ANSWER_JSON}",
    f"```yaml\n{ANSWER_JSON}\n```",
    f"{ANSWER_JSON}\nnull",
    [{"type": "text", "text": ANSWER_JSON}, {"type": "text", "text": ANSWER_JSON}],
    '{"blocks":[{"id":"p2b1","audience":"student","audience":"teacher"}]}',
    '{"blocks":[],"blocks":[]}',
    '{"score":NaN}',
    '{,}',
    '{"options":[,]}',
    '{"blocks":[{},,]}',
    '{"text":,}',
])
def test_pdf_completion_rejects_ambiguous_incomplete_or_invalid_json(content):
    with pytest.raises(APIError) as error:
        solar._completion_object(completion(content), for_pdf=True, page=2)
    assert error.value.code == "SOLAR_OUTPUT_JSON_INVALID"


@pytest.mark.parametrize(("response", "code", "reason"), [
    (completion(ANSWER_JSON, reason="length"), "SOLAR_OUTPUT_TRUNCATED", "length"),
    (completion(ANSWER_JSON, reason="content_filter"), "SOLAR_OUTPUT_FILTERED", "content_filter"),
    (completion(None, refusal="provider-private-text"), "SOLAR_OUTPUT_FILTERED", "refusal"),
    (completion([{"type": "refusal", "refusal": "provider-private-text"}]), "SOLAR_OUTPUT_FILTERED", "refusal"),
    (completion([{"type": "text", "text": ANSWER_JSON}, {"type": "unknown", "text": "provider-private-text"}]),
     "SOLAR_RESPONSE_INVALID", "content_shape"),
    (completion(None, reasoning_content="provider-private-text"), "SOLAR_OUTPUT_EMPTY", "empty_content"),
    (completion(["provider-private-text"]), "SOLAR_RESPONSE_INVALID", "content_shape"),
    (httpx.Response(200, json={"choices": [{"message": {"other": "provider-private-text"}}]}),
     "SOLAR_RESPONSE_INVALID", "missing_content"),
    (httpx.Response(200, json={"choices": [{"message": {"refusal": "provider-private-text"}}]}),
     "SOLAR_OUTPUT_FILTERED", "refusal"),
    (httpx.Response(200, json={"choices": [], "other": "provider-private-text"}),
     "SOLAR_RESPONSE_INVALID", "response_shape"),
    (httpx.Response(200, content='{"choices":[{"message":{"content":"{}","content":"provider-private-text"}}]}'),
     "SOLAR_RESPONSE_INVALID", "response_shape"),
    (completion('{"blocks":[provider-private-text', reason="provider-private-text"),
     "SOLAR_OUTPUT_JSON_INVALID", "invalid_json"),
])
def test_pdf_output_failures_report_shape_without_provider_text(response, code, reason, caplog):
    with pytest.raises(APIError) as error:
        solar._completion_object(response, for_pdf=True, page=2)
    assert error.value.code == code
    assert f"page=2, reason={reason}, size=" in caplog.text
    assert "provider-private-text" not in caplog.text


def test_pdf_client_uses_recovery_without_changing_non_pdf_parsing(monkeypatch):
    original_client = httpx.Client
    requests = []

    def handler(request):
        requests.append(request)
        return completion(f"결과입니다.\n{ANSWER_JSON}")

    monkeypatch.setattr(solar.httpx, "Client", lambda **options: original_client(
        transport=httpx.MockTransport(handler), **options,
    ))
    assert solar.complete_json("test-key", solar.DEFAULT_MODEL, "지시", {"page": 2}, for_pdf=True) == ANSWER
    with pytest.raises(APIError) as error:
        solar.complete_json("test-key", solar.DEFAULT_MODEL, "지시", {"page": 2})
    assert error.value.code == "SOLAR_OUTPUT_JSON_INVALID"
    assert len(requests) == 2
