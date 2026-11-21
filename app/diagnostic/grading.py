from collections import defaultdict
from collections.abc import Mapping
from decimal import Decimal

from app.core.dto import Area, Level

from .dto import AreaResult, DiagnosticResults, PartResult, Skill
from .forms import AREAS, SKILLS, DiagnosticForm, DiagnosticItem


def part_level(score: Decimal, beginner: int, intermediate: int, advanced: int) -> Level:
    if beginner < 1:
        return "below_beginner"
    if score >= 70 and intermediate == 2 and advanced == 2:
        return "advanced"
    if score >= 40 and intermediate == 2:
        return "intermediate"
    return "beginner"


def aggregate_level(listening: Decimal, reading: Decimal) -> Level:
    total = listening + reading
    if min(listening, reading) < 30 or total < 80:
        return "beginner"
    if total < 140:
        return "intermediate"
    return "advanced"


def grade(form: DiagnosticForm, answers: Mapping[str, str | None]) -> DiagnosticResults:
    if answers.keys() != {question.display.id for question in form.questions}:
        raise ValueError("Grading requires exactly one answer for every question")

    correct_by_part: dict[tuple[Area, Skill], list[DiagnosticItem]] = defaultdict(list)
    for item in form.questions:
        if answers[item.display.id] == item.correct_option_id:
            correct_by_part[item.display.area, item.display.skill].append(item)

    parts: list[PartResult] = []
    areas: list[AreaResult] = []
    for area in AREAS:
        area_scores: list[Decimal] = []
        for skill in SKILLS:
            correct = correct_by_part[area, skill]
            score = sum((item.max_points for item in correct), Decimal(0))
            gates = [item.tier for item in correct if item.gate_slot is not None]
            level = part_level(
                score, gates.count("beginner"), gates.count("intermediate"), gates.count("advanced")
            )
            parts.append(PartResult(area=area, skill=skill, score=float(score), level=level))
            area_scores.append(score)

        listening, reading = area_scores
        areas.append(AreaResult(
            area=area, score=float(listening + reading), level=aggregate_level(listening, reading)
        ))

    return DiagnosticResults(formId=form.id, isSynthetic=form.is_synthetic, parts=parts, areas=areas)
