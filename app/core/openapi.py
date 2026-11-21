from fastapi import FastAPI


def add_request_headers(app: FastAPI) -> None:
    """Describe headers validated by shared guards without changing their HTTP errors."""
    generate_schema = app.openapi

    def schema():
        if app.openapi_schema is not None:
            return app.openapi_schema
        document = generate_schema()
        for path, operations in document["paths"].items():
            for method, operation in operations.items():
                if method in {"post", "patch"} and path != "/api/v1/auth/login":
                    _add_header(operation, "X-CSRF-Token", "로그인 또는 /auth/me 응답의 csrfToken")
                if method == "patch" or (method == "post" and path.endswith(("/review", "/publish"))):
                    _add_header(operation, "If-Match", "상세 조회 또는 직전 변경 응답의 ETag 원문")
                if path == "/api/v1/quizzes/{quizId}/answers":
                    for parameter in operation["parameters"]:
                        if parameter["name"] == "Idempotency-Key":
                            parameter.update(
                                required=True,
                                description="같은 답안을 재전송할 때 재사용하는 요청키",
                                schema={"type": "string", "minLength": 1, "maxLength": 64},
                            )
        return document

    app.openapi = schema


def _add_header(operation: dict, name: str, description: str) -> None:
    operation.setdefault("parameters", []).append({
        "name": name,
        "in": "header",
        "required": True,
        "description": description,
        "schema": {"type": "string"},
    })
