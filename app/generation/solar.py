"""Server-side client for the Upstage Chat Completions API."""

import json
import os
from typing import Any

import httpx

from app.core.errors import fail

SOLAR_URL = "https://api.upstage.ai/v1/chat/completions"
DEFAULT_MODEL = "solar-pro-4"

ERROR_MESSAGES = {
    "SOLAR_KEY_REQUIRED": "교사 계정의 Solar API 키를 등록해 주세요.",
    "SOLAR_KEY_INVALID": "Solar에서 API 키를 거부했습니다. 키와 사용 권한을 확인해 주세요.",
    "SOLAR_ENCRYPTION_REQUIRED": "서버에 Solar 키 암호화 설정이 필요합니다.",
    "SOLAR_MODEL_UNAVAILABLE": "Solar 모델 ID와 이 키의 모델 사용 권한을 확인해 주세요.",
    "SOLAR_LIMIT": "Solar 사용량 한도 또는 결제 상태를 확인해 주세요.",
    "SOLAR_TIMEOUT": "Solar 응답을 받지 못했습니다. 요청이 처리되었을 수 있으니 확인 후 다시 시도해 주세요.",
    "SOLAR_UNAVAILABLE": "Solar 연결이 불안정합니다. 잠시 후 다시 시도해 주세요.",
    "SOLAR_OUTPUT_INVALID": "Solar 응답을 자료 형식으로 읽을 수 없습니다.",
    "GENERATION_INTERRUPTED": "생성 작업을 마치지 못했습니다. 상태를 확인한 뒤 새 요청으로 다시 시도해 주세요.",
    "DATABASE_UNAVAILABLE": "서버의 저장 연결을 확인해 주세요. 작업이 처리되었을 수 있습니다.",
    "GENERATION_FAILED": "초안을 저장하지 못했습니다. 담당 교과와 학생의 상태를 확인해 주세요.",
    "SOURCE_CHANGED": "원본 자료 또는 대상 학생에 대한 접근이 변경되었습니다.",
    "PDF_PAGE_RANGE": "PDF의 실제 쪽 범위를 확인해 주세요.",
    "PDF_TEXT_REQUIRED": "선택한 쪽에서 PDF 본문을 읽을 수 없습니다.",
    "GENERATION_INPUT_TOO_LARGE": "자료가 길어 한 번에 처리할 수 없습니다. 페이지 범위를 줄이거나 짧은 PDF를 선택해 주세요.",
}


def model_name() -> str:
    model = os.getenv("GANADA_SOLAR_MODEL", DEFAULT_MODEL).strip()
    if not model or len(model) > 100:
        fail(503, "SOLAR_MODEL_UNAVAILABLE", ERROR_MESSAGES["SOLAR_MODEL_UNAVAILABLE"])
    return model


def complete_json(api_key: str, model: str, instructions: str, data: dict[str, Any]) -> dict:
    """Call Solar once. The calling workflow owns retry and output validation."""
    try:
        with httpx.Client(timeout=httpx.Timeout(60.0, connect=8.0)) as client:
            response = client.post(
                SOLAR_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": instructions},
                        {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
                    ],
                    "max_tokens": 8000,
                },
            )
    except httpx.TimeoutException:
        fail(504, "SOLAR_TIMEOUT", ERROR_MESSAGES["SOLAR_TIMEOUT"])
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

    try:
        choice = response.json()["choices"][0]
        if choice.get("finish_reason") in {"length", "content_filter"}:
            raise ValueError("unfinished response")
        text = choice["message"]["content"].strip()
        if text.startswith("```") and text.endswith("```"):
            text = text.split("\n", 1)[1][:-3].strip()
        result = json.loads(text)
        if not isinstance(result, dict):
            raise ValueError("expected an object")
        return result
    except (KeyError, IndexError, TypeError, AttributeError, ValueError):
        fail(502, "SOLAR_OUTPUT_INVALID", ERROR_MESSAGES["SOLAR_OUTPUT_INVALID"])
