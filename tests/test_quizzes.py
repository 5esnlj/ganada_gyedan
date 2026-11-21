from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack

import pytest
from fastapi.testclient import TestClient

from app.core.errors import APIError
from app.main import create_app
from app.quizzes.dto import QuizDraft
from app.quizzes.service import create_quiz_draft

QUIZ_A = "/quizzes/quiz_a_math_1"
QUIZ_B = "/quizzes/quiz_b_math_1"


@pytest.fixture
def clients():
    app = create_app()
    with ExitStack() as stack:
        def session(login_id):
            client = stack.enter_context(TestClient(app, base_url="http://testserver/api/v1"))
            result = client.post(
                "/auth/login",
                json={
                    "loginId": login_id,
                    "password": "mock-pass-123",
                    "role": "teacher" if login_id.startswith("t_") else "student",
                },
            )
            assert result.status_code == 200, result.text
            client.headers["X-CSRF-Token"] = result.json()["csrfToken"]
            return client

        yield app, session


def test_student_quiz_scope_and_explanations(clients):
    _, session = clients
    student = session("s_a")
    teacher = session("t_math")
    response = student.get("/classes/cls_1/quizzes")
    assert response.status_code == 200, response.text
    assert [item["subject"] for item in response.json()["items"]] == ["math", "science"]
    for item in response.json()["items"]:
        assert item["passage"]["paragraphs"]
        for question in item["questions"]:
            assert {"correctOptionId", "explanation", "sourceSentenceIds"}.isdisjoint(question)

    assert student.get(QUIZ_B).status_code == 404
    assert session("s_b").get(QUIZ_A).status_code == 404
    assert session("s_pending").get(QUIZ_A).status_code == 403
    assert teacher.get("/quizzes/quiz_a_science_1").status_code == 404
    assert teacher.get("/quizzes/quiz_outside_1").status_code == 404
    assert teacher.get(QUIZ_A).json()["questions"][0]["correctOptionId"] == "1"

    explanation = f"{QUIZ_A}/questions/quiz_a_math_1_q1/explanation"
    assert student.get(explanation).json()["code"] == "ANSWER_REQUIRED"
    answer = student.post(
        f"{QUIZ_A}/answers",
        json={"questionId": "quiz_a_math_1_q1", "selectedOptionId": "2"},
        headers={"Idempotency-Key": "first-q1"},
    )
    assert answer.status_code == 200, answer.text
    assert student.get(explanation).json()["correctOptionId"] == "1"
    assert "correctOptionId" not in student.get(QUIZ_A).json()["questions"][0]


def test_edit_clears_review_and_publish_locks_questions(clients):
    _, session = clients
    teacher = session("t_math")
    student = session("s_b")
    current = teacher.get(QUIZ_B)
    initial = current.headers["ETag"]
    question = current.json()["questions"][0]
    question_path = f"{QUIZ_B}/questions/{question['id']}"
    editable = {
        key: question[key]
        for key in ("prompt", "choices", "correctOptionId", "explanation", "sourceSentenceIds", "maxPoints")
    }
    assert student.get(QUIZ_B).status_code == 404
    assert teacher.post(f"{QUIZ_B}/review").status_code == 428
    reviewed = teacher.post(f"{QUIZ_B}/review", headers={"If-Match": initial})
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["status"] == "reviewed"
    assert teacher.patch(question_path, headers={"If-Match": initial}, json=editable).status_code == 412

    review_tag = reviewed.headers["ETag"]
    wrong_source = {**editable, "sourceSentenceIds": ["no-such-sentence"]}
    invalid = teacher.patch(question_path, headers={"If-Match": review_tag}, json=wrong_source)
    assert invalid.status_code == 422, invalid.text
    assert teacher.get(QUIZ_B).headers["ETag"] == review_tag

    edited = teacher.patch(
        question_path, headers={"If-Match": review_tag}, json={**editable, "prompt": "수정한 예시 문항"}
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["status"] == "draft"
    assert edited.json()["reviewedAt"] is None
    new_version = {"If-Match": edited.headers["ETag"]}
    assert teacher.post(f"{QUIZ_B}/publish", headers=new_version).status_code == 409
    second_review = teacher.post(f"{QUIZ_B}/review", headers=new_version)
    published = teacher.post(f"{QUIZ_B}/publish", headers={"If-Match": second_review.headers["ETag"]})
    assert published.status_code == 200, published.text
    assert student.get(QUIZ_B).json()["questions"][0]["prompt"] == "수정한 예시 문항"
    immutable = teacher.patch(question_path, headers={"If-Match": published.headers["ETag"]}, json=editable)
    assert immutable.json()["code"] == "PUBLISHED_IMMUTABLE"


def test_retries_preserve_weighted_first_score_and_original_replay(clients):
    app, session = clients
    student = session("s_a")
    questions = app.state.store.quizzes["quiz_a_math_1"]["questions"]
    for question, weight in zip(questions, [2, 3, 5], strict=True):
        question["maxPoints"] = weight

    def answer(index, option, key):
        return student.post(
            f"{QUIZ_A}/answers",
            json={"questionId": f"quiz_a_math_1_q{index}", "selectedOptionId": option},
            headers={"Idempotency-Key": key},
        )

    first = answer(1, "2", "q1-first")
    assert first.status_code == 200, first.text
    early_retry = answer(1, "1", "q1-retry")
    assert early_retry.json()["results"]["firstScore"] is None
    assert early_retry.json()["retryCount"] == 1
    assert answer(1, "1", "q1-retry-2").json()["code"] == "ALREADY_CORRECT"
    assert answer(2, "1", "q1-first").json()["code"] == "IDEMPOTENCY_CONFLICT"
    assert answer(2, "1", "q2-first").status_code == 200
    last = answer(3, "1", "q3-first").json()["results"]
    assert last["status"] == "completed"
    assert last["firstScore"] == 8
    assert last["maxScore"] == 10
    assert last["firstCorrectCount"] == 2
    assert last["currentWrongCount"] == 0
    assert last["latestCorrectCount"] == 3
    assert answer(1, "2", "q1-first").json() == {**first.json(), "replayed": True}
    assert student.get(f"{QUIZ_A}/results").json() == last


def test_concurrent_answer_retries_record_one_attempt(clients):
    app, session = clients
    student = session("s_a")
    payload = {"questionId": "quiz_a_math_1_q1", "selectedOptionId": "2"}

    def submit():
        return student.post(
            f"{QUIZ_A}/answers", json=payload, headers={"Idempotency-Key": "same-attempt"}
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = [executor.submit(submit), executor.submit(submit)]
        results = [response.result() for response in responses]

    assert all(response.status_code == 200 for response in results)
    assert sorted(response.json()["replayed"] for response in results) == [False, True]
    assert all(response.json()["attemptNo"] == 1 for response in results)
    assert len(app.state.store.attempts["quiz_a_math_1"]["quiz_a_math_1_q1"]) == 1


def test_generated_draft_handoff_checks_input_identity_and_validates_choices(clients):
    app, session = clients
    state = app.state.store
    teacher = state.users["usr_t_math"]
    body = QuizDraft(
        sourceId="src_math_1",
        studentMemberId="mem_s_a",
        inputMaterialId="mat_a_math_1",
        title="자료 기반 OX 확인 문제",
        questionType="true_false",
        questions=[
            {
                "prompt": "지문과 일치하나요?",
                "choices": [{"id": "yes", "text": "맞아요"}, {"id": "no", "text": "아니에요"}],
                "correctOptionId": "yes",
                "explanation": "첫 문장을 읽어 보세요.",
                "sourceSentenceIds": ["s1"],
            }
        ],
    )
    with pytest.raises(APIError, match="입력 전환본을 찾을 수 없습니다"):
        create_quiz_draft(state, teacher, body.model_copy(update={"inputMaterialId": "mat_b_math_1"}))

    pending_student = body.model_copy(update={"studentMemberId": "mem_s_pending", "inputMaterialId": None})
    with pytest.raises(APIError, match="최초 진단"):
        create_quiz_draft(state, teacher, pending_student)

    invalid_question = body.questions[0].model_copy(update={"correctOptionId": "missing"})
    with pytest.raises(APIError, match="보기 수, 정답"):
        create_quiz_draft(state, teacher, body.model_copy(update={"questions": [invalid_question]}))

    quiz = create_quiz_draft(state, teacher, body)
    assert quiz["generationNo"] == 2
    browser = session("t_math")
    path = f"/quizzes/{quiz['id']}"
    original = browser.get(path)
    assert original.status_code == 200, original.text
    reviewed = browser.post(f"{path}/review", headers={"If-Match": original.headers["ETag"]})
    assert reviewed.status_code == 200, reviewed.text
    published = browser.post(f"{path}/publish", headers={"If-Match": reviewed.headers["ETag"]})
    assert published.status_code == 200, published.text
    student = session("s_a")
    assert student.get(path).json()["passage"] == state.materials["mat_a_math_1"]["content"]
    state.materials["mat_a_math_1"]["status"] = "withdrawn"
    assert student.get(path).status_code == 404
