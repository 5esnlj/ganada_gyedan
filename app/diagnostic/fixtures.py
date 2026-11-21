"""Synthetic question data exclusively for local API and frontend integration."""

from app.core.state import MemoryStore, timestamp

from .dto import DiagnosticOption, QuestionDisplay
from .forms import AREAS, SKILLS, DiagnosticForm, DiagnosticItem, FormProvider, StaticFormProvider
from .grading import grade
from .service import new_diagnostic


def demo_provider() -> StaticFormProvider:
    items = []
    rubric_sample = (
        ("beginner", 1, 5),
        ("beginner", 2, 5),
        ("intermediate", 1, 15),
        ("intermediate", 2, 15),
        ("advanced", 1, 15),
        ("advanced", 2, 15),
        ("advanced", None, 30),
    )
    choices = tuple(DiagnosticOption(id=str(i), text=f"가상 보기 {i}") for i in range(1, 5))
    for area in AREAS:
        for skill in SKILLS:
            for position, (tier, gate, points) in enumerate(rubric_sample, 1):
                sequence = len(items) + 1
                items.append(DiagnosticItem(
                    display=QuestionDisplay(
                        id=f"dq_{sequence:03}", sequence=sequence, area=area, skill=skill,
                        prompt=f"[테스트 전용] {area} {skill} {position}번 문항",
                        options=choices, audioUrl=None, stimulusText="연동 검증용 가상 지문입니다.",
                    ),
                    correct_option_id="1", tier=tier, gate_slot=gate, max_points=points,
                ))
    return StaticFormProvider(DiagnosticForm(id="form_mock_v1", is_synthetic=True, questions=items))


def seed_diagnostics(state: MemoryStore, provider: FormProvider) -> None:
    form = provider.active_form()
    if form is None:
        return

    for student_id in ("usr_s_a", "usr_s_b", "usr_s_pending", "usr_s_outside"):
        if student_id not in state.users:
            continue
        enrolled = next(
            (member for member in state.memberships.values() if member["userId"] == student_id),
            None,
        )
        if enrolled is None:
            continue

        diagnostic = new_diagnostic(form, enrolled["classId"])
        if student_id != "usr_s_pending":
            answers = {
                item.display.id: _fixture_answer(student_id, item)
                for item in form.questions
            }
            diagnostic.update(
                status="completed", completedAt=timestamp(), answers=answers, results=grade(form, answers)
            )
        state.diagnostics[student_id] = diagnostic


def _fixture_answer(student_id: str, item: DiagnosticItem) -> str | None:
    mixed_answers = {
        "korean": (0, 0, 0, 0, 0, 0, 0),
        "math": (1, 1, 1, 1, 0, 0, 0),
        "social": (1, 1, 1, 1, 1, 1, 1),
        "science": (1, 0, 1, 0, 1, 1, 1),
        "daily": (1, 0, 0, 0, 0, 0, 0),
    }
    if student_id == "usr_s_b":
        sample = mixed_answers[item.display.area]
        return item.correct_option_id if sample[(item.display.sequence - 1) % len(sample)] else None
    return item.correct_option_id
