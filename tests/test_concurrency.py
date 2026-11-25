from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from threading import Barrier

from fastapi.testclient import TestClient

from app.generation.dto import Generate
from app.generation.jobs import request_keys
from app.main import create_app


def test_legacy_pdf_request_keeps_its_idempotency_digest():
    legacy = {
        "sourceId": "src_1", "studentMemberIds": ["mem_1"], "operation": "convert",
        "pages": [{"number": 1, "blocks": [{"id": "p1b1", "text": "자료를 읽어 보세요.", "preserve": False}]}],
    }
    digest = sha256(json.dumps(legacy, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    assert request_keys(Generate.model_validate(legacy), "same-request")[1] == digest
    legacy["pages"][0]["blocks"][0]["layout"] = "text"
    assert request_keys(Generate.model_validate(legacy), "same-request")[1] != digest


def test_concurrent_material_edits_with_same_etag_allow_one_update():
    with TestClient(create_app()) as client:
        session = client.post("/api/v1/auth/login", json={
            "loginId": "t_math", "password": "mock-pass-123", "role": "teacher",
        })
        assert session.status_code == 200
        route = "/api/v1/materials/mat_b_math_1"
        before = client.get(route)
        assert before.status_code == 200
        barrier = Barrier(2, timeout=5)
        headers = {"X-CSRF-Token": session.json()["csrfToken"], "If-Match": before.headers["ETag"]}

        def submit(text):
            barrier.wait()
            body = {"content": {"paragraphs": [{"id": "s1", "text": text}]}}
            return client.patch(route, json=body, headers=headers)

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.submit(submit, "첫 번째 변경"), pool.submit(submit, "두 번째 변경")
            responses = [first.result(timeout=10), second.result(timeout=10)]

        assert sorted(response.status_code for response in responses) == [200, 412]
        winner = next(response for response in responses if response.status_code == 200)
        current = client.get(route)
        assert current.json()["content"] == winner.json()["content"]
        assert current.headers["ETag"] == winner.headers["ETag"]
        assert current.json()["version"] == before.json()["version"] + 1
