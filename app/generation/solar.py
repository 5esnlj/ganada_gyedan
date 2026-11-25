"""Server-side client for the Upstage Chat Completions API."""

import json
import logging
import os
import re
from time import monotonic
from typing import Any

import httpx

from app.core.errors import fail

SOLAR_URL = "https://api.upstage.ai/v1/chat/completions"
DEFAULT_MODEL = "solar-pro4"
FENCED_JSON = re.compile(r"^[ \t]*```(?:json)?[ \t]*\r?\n(.*?)```[ \t]*$", re.I | re.M | re.S)
PDF_JSON_FENCE = re.compile(
    r"^[ \t]*```(?:json)?[ \t]*\r?\n(.*?)^[ \t]*```[ \t]*\r?$",
    re.I | re.M | re.S,
)
FINISH_ERRORS = {"length": "SOLAR_OUTPUT_TRUNCATED", "content_filter": "SOLAR_OUTPUT_FILTERED"}
PDF_READ_TIMEOUT = 180.0
logger = logging.getLogger(__name__)

ERROR_MESSAGES = {
    "SOLAR_KEY_REQUIRED": "교사 계정의 Solar API 키를 등록해 주세요.",
    "SOLAR_KEY_INVALID": "Solar에서 API 키를 거부했습니다. 키와 사용 권한을 확인해 주세요.",
    "SOLAR_ENCRYPTION_REQUIRED": "서버에 Solar 키 암호화 설정이 필요합니다.",
    "SOLAR_MODEL_UNAVAILABLE": "Solar 모델 ID와 이 키의 모델 사용 권한을 확인해 주세요.",
    "SOLAR_LIMIT": "Solar 사용량 한도 또는 결제 상태를 확인해 주세요.",
    "SOLAR_TIMEOUT": "Solar 응답을 받지 못했습니다. 요청이 처리되었을 수 있으니 확인 후 다시 시도해 주세요.",
    "SOLAR_CONNECT_TIMEOUT": "Solar 서버에 연결하는 시간이 초과됐습니다. 백엔드를 실행한 환경의 인터넷 연결을 확인해 주세요.",
    "SOLAR_READ_TIMEOUT": "Solar 답변이 대기 시간 안에 도착하지 않았습니다. Solar에서 이미 요청을 처리했을 수 있습니다.",
    "SOLAR_UNAVAILABLE": "Solar 연결이 불안정합니다. 잠시 후 다시 시도해 주세요.",
    "SOLAR_RESPONSE_INVALID": "Solar 응답에서 답변 형식을 확인할 수 없습니다.",
    "SOLAR_OUTPUT_TRUNCATED": "Solar 답변이 토큰 한도에서 중단됐습니다. 이 쪽만 다시 시도하고, 계속 실패하면 더 짧은 자료를 사용해 주세요.",
    "SOLAR_OUTPUT_FILTERED": "Solar가 이 쪽의 답변을 제한했습니다. 원문 내용을 확인해 주세요.",
    "SOLAR_OUTPUT_EMPTY": "Solar가 최종 답변을 보내지 않았습니다. 이 쪽만 다시 시도해 주세요.",
    "SOLAR_OUTPUT_INVALID": "Solar 응답을 자료 형식으로 읽을 수 없습니다.",
    "SOLAR_OUTPUT_JSON_INVALID": "Solar 답변이 JSON 객체 형식에 맞지 않습니다. 이 쪽만 다시 시도해 주세요.",
    "SOLAR_OUTPUT_SCHEMA": "Solar가 문단 분류나 용어 풀이를 요청한 형식으로 반환하지 않았습니다. 이 쪽만 다시 시도해 주세요.",
    "SOLAR_OUTPUT_BLOCKS": "Solar 답변의 문단 ID가 원본과 일치하지 않습니다. 이 쪽만 다시 시도해 주세요.",
    "GENERATION_INTERRUPTED": "생성 작업을 마치지 못했습니다. 상태를 확인한 뒤 새 요청으로 다시 시도해 주세요.",
    "DATABASE_UNAVAILABLE": "서버의 저장 연결을 확인해 주세요. 작업이 처리되었을 수 있습니다.",
    "GENERATION_FAILED": "초안을 저장하지 못했습니다. 담당 교과와 학생의 상태를 확인해 주세요.",
    "SOURCE_CHANGED": "원본 자료 또는 대상 학생에 대한 접근이 변경되었습니다.",
    "PDF_PAGE_RANGE": "PDF의 실제 쪽 범위를 확인해 주세요.",
    "PDF_TEXT_REQUIRED": "선택한 쪽에서 PDF 본문을 읽을 수 없습니다.",
    "PDF_NO_STUDENT_CONTENT": "선택한 쪽에 학생용으로 남길 문단이 없습니다. 본문이 있는 쪽을 선택해 주세요.",
    "GENERATION_INPUT_TOO_LARGE": "자료가 길어 한 번에 처리할 수 없습니다. 페이지 범위를 줄이거나 짧은 PDF를 선택해 주세요.",
}


def _canonical_model(model: str) -> str:
    return DEFAULT_MODEL if model == "solar-pro-4" else model


def model_name() -> str:
    model = os.getenv("GANADA_SOLAR_MODEL", DEFAULT_MODEL).strip()
    if not model or len(model) > 100:
        fail(503, "SOLAR_MODEL_UNAVAILABLE", ERROR_MESSAGES["SOLAR_MODEL_UNAVAILABLE"])
    return _canonical_model(model)


def complete_json(api_key: str, model: str, instructions: str, data: dict[str, Any], *, for_pdf: bool = False) -> dict:
    """Call Solar once. The calling workflow owns retry and output validation."""
    model = _canonical_model(model)
    pro4 = model == DEFAULT_MODEL or model.startswith(f"{DEFAULT_MODEL}-")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
        ],
        "max_tokens": 8000,
    }
    if pro4:
        # PDF output uses a fixed classification and annotation contract.
        payload.update(reasoning_effort="none" if for_pdf else "low", max_tokens=16_384)

    default_timeout = 120.0 if pro4 else 60.0
    timeout = httpx.Timeout(default_timeout, connect=8.0, read=PDF_READ_TIMEOUT if for_pdf else default_timeout)
    started = monotonic()
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(
                SOLAR_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
    except httpx.TimeoutException as error:
        code = "SOLAR_TIMEOUT"
        if for_pdf:
            if isinstance(error, httpx.ConnectTimeout):
                code = "SOLAR_CONNECT_TIMEOUT"
            elif isinstance(error, httpx.ReadTimeout):
                code = "SOLAR_READ_TIMEOUT"
            logger.warning("Solar PDF request timed out (page=%s, phase=%s, elapsed=%.1fs)",
                           data.get("page"), type(error).__name__, monotonic() - started)
        fail(504, code, ERROR_MESSAGES[code])
    except httpx.RequestError:
        fail(502, "SOLAR_UNAVAILABLE", ERROR_MESSAGES["SOLAR_UNAVAILABLE"])

    if response.status_code in {401, 403}:
        fail(502, "SOLAR_KEY_INVALID", ERROR_MESSAGES["SOLAR_KEY_INVALID"])
    if response.status_code == 404:
        fail(502, "SOLAR_MODEL_UNAVAILABLE", ERROR_MESSAGES["SOLAR_MODEL_UNAVAILABLE"])
    if response.status_code in {402, 429}:
        fail(502, "SOLAR_LIMIT", ERROR_MESSAGES["SOLAR_LIMIT"])
    if not response.is_success:
        fail(502, "SOLAR_UNAVAILABLE", ERROR_MESSAGES["SOLAR_UNAVAILABLE"])

    return _completion_object(response, for_pdf=for_pdf, page=data.get("page") if for_pdf else None)


class _PdfJsonError(ValueError):
    """A fixed reason suitable for logging without including provider content."""


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise _PdfJsonError("duplicate_key")
        result[key] = value
    return result


def _reject_constant(_constant: str) -> None:
    raise _PdfJsonError("invalid_constant")


PDF_JSON_DECODER = json.JSONDecoder(object_pairs_hook=_unique_keys, parse_constant=_reject_constant)


def _strip_trailing_commas(text: str) -> str:
    # Change only a comma following a value before a closing bracket or brace.
    chars = list(text)
    quoted = escaped = False
    previous = ""
    for index, character in enumerate(text):
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
            continue
        if character == '"':
            quoted = True
        elif character == "," and previous and previous not in "{[,:":
            end = index + 1
            while end < len(text) and text[end] in " \t\r\n":
                end += 1
            if end < len(text) and text[end] in "]}":
                chars[index] = " "
        if character not in " \t\r\n":
            previous = character
    return "".join(chars)


def _pdf_prose(text: str) -> bool:
    text = text.strip()
    if not text or text in {".", "。"}:
        return True
    if any(character in text for character in '{}[]"\x60\ufeff'):
        return False
    if text.strip(".,:!?;… ").lower() in {"null", "true", "false", "nan", "infinity", "-infinity"}:
        return False
    return any(character.isalpha() for character in text)


def _clean_pdf_json(text: str) -> str:
    return text.strip().removeprefix("\ufeff").strip()


def _pdf_completion_json(content: str) -> dict:
    text = _clean_pdf_json(content)
    fences = list(PDF_JSON_FENCE.finditer(text))
    if fences:
        fence = fences[0]
        if len(fences) != 1 or not _pdf_prose(text[:fence.start()]) or not _pdf_prose(text[fence.end():]):
            raise _PdfJsonError("ambiguous_envelope")
        value = PDF_JSON_DECODER.decode(_strip_trailing_commas(_clean_pdf_json(fence[1])))
    else:
        # Start at the outermost value, so a valid nested object cannot rescue a
        # truncated outer response. Encoded strings must consume the whole text.
        start = re.search(r"[{\[]", text) if not text.startswith('"') else None
        if start is None and not text.startswith('"'):
            raise _PdfJsonError("no_json")
        offset = start.start() if start else 0
        if not _pdf_prose(text[:offset]):
            raise _PdfJsonError("ambiguous_envelope")
        candidate = _strip_trailing_commas(text[offset:])
        value, end = PDF_JSON_DECODER.raw_decode(candidate)
        if not _pdf_prose(candidate[end:]) or (isinstance(value, str) and candidate[end:].strip()):
            raise _PdfJsonError("ambiguous_envelope")

    if isinstance(value, str):
        value = PDF_JSON_DECODER.decode(_strip_trailing_commas(_clean_pdf_json(value)))
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    if not isinstance(value, dict):
        raise _PdfJsonError("not_one_object")
    return value


def _log_pdf_completion(page: object, reason: str, size: int) -> None:
    logger.warning("Solar PDF completion rejected (page=%s, reason=%s, size=%s)",
                   page if type(page) is int else None, reason, size)


def _pdf_text_parts(content: list, page: object, size: int) -> str | list:
    if any(isinstance(part, dict) and part.get("type") == "refusal" for part in content):
        _log_pdf_completion(page, "refusal", size)
        fail(502, "SOLAR_OUTPUT_FILTERED", ERROR_MESSAGES["SOLAR_OUTPUT_FILTERED"])
    if content and all(
        isinstance(part, dict) and part.keys() == {"type", "text"}
        and part["type"] == "text" and isinstance(part["text"], str)
        for part in content
    ):
        return "".join(part["text"] for part in content)
    return content


def _completion_object(response: httpx.Response, *, for_pdf: bool = False, page: object = None) -> dict:
    try:
        envelope = response.json(object_pairs_hook=_unique_keys) if for_pdf else response.json()
        choice = envelope["choices"][0]
        finish_reason = choice.get("finish_reason")
    except (KeyError, IndexError, TypeError, AttributeError, ValueError):
        if for_pdf:
            _log_pdf_completion(page, "response_shape", len(response.content))
        fail(502, "SOLAR_RESPONSE_INVALID", ERROR_MESSAGES["SOLAR_RESPONSE_INVALID"])

    if isinstance(finish_reason, str) and finish_reason in FINISH_ERRORS:
        code = FINISH_ERRORS[finish_reason]
        if for_pdf:
            _log_pdf_completion(page, finish_reason, len(response.content))
        fail(502, code, ERROR_MESSAGES[code])

    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if for_pdf and isinstance(content, list):
        content = _pdf_text_parts(content, page, len(response.content))
    if for_pdf and isinstance(message, dict):
        refusal = message.get("refusal")
        no_answer = content is None or content == [] or (isinstance(content, str) and not content.strip())
        if isinstance(refusal, str) and refusal.strip() and no_answer:
            _log_pdf_completion(page, "refusal", len(response.content))
            fail(502, "SOLAR_OUTPUT_FILTERED", ERROR_MESSAGES["SOLAR_OUTPUT_FILTERED"])

    if not isinstance(message, dict) or "content" not in message:
        if for_pdf:
            _log_pdf_completion(page, "missing_content", len(response.content))
        fail(502, "SOLAR_RESPONSE_INVALID", ERROR_MESSAGES["SOLAR_RESPONSE_INVALID"])
    if content is None or (isinstance(content, str) and not content.strip()):
        if for_pdf:
            _log_pdf_completion(page, "empty_content", len(content or ""))
        fail(502, "SOLAR_OUTPUT_EMPTY", ERROR_MESSAGES["SOLAR_OUTPUT_EMPTY"])
    if not isinstance(content, str):
        if for_pdf:
            _log_pdf_completion(page, "content_shape", len(response.content))
        fail(502, "SOLAR_RESPONSE_INVALID", ERROR_MESSAGES["SOLAR_RESPONSE_INVALID"])

    if for_pdf:
        try:
            return _pdf_completion_json(content)
        except _PdfJsonError as error:
            _log_pdf_completion(page, str(error), len(content))
        except (ValueError, RecursionError):
            _log_pdf_completion(page, "invalid_json", len(content))
        fail(502, "SOLAR_OUTPUT_JSON_INVALID", ERROR_MESSAGES["SOLAR_OUTPUT_JSON_INVALID"])

    text = content.strip()
    if not text.startswith("{"):
        fenced = FENCED_JSON.findall(text)
        if len(fenced) == 1:
            text = fenced[0]
    try:
        result = json.loads(text)
    except ValueError:
        fail(502, "SOLAR_OUTPUT_JSON_INVALID", ERROR_MESSAGES["SOLAR_OUTPUT_JSON_INVALID"])
    if not isinstance(result, dict):
        fail(502, "SOLAR_OUTPUT_JSON_INVALID", ERROR_MESSAGES["SOLAR_OUTPUT_JSON_INVALID"])
    return result
