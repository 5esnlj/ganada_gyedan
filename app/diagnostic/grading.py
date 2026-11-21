from collections import defaultdict
from collections.abc import Mapping
from decimal import Decimal

from app.core.dto import Area, Level

from .dto import (
    AreaResult,
    AssessmentCommunicationResult,
    AssessmentOverallResult,
    AssessmentSkillResult,
    AssessmentSubjectResult,
    AssessmentSummary,
    AssessmentTierCount,
    DiagnosticResults,
    PartResult,
    Skill,
    Tier,
)
from .forms import AREAS, SKILLS, TIERS, DiagnosticForm, DiagnosticItem


LEVEL_ORDER: tuple[Level, ...] = ("below_beginner", *TIERS)


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

    if form.rubric == "korean-assessment-v1":
        return _grade_assessment(form, answers)

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
            parts.append(PartResult(area=area, skill=skill, score=float(score), maxScore=100, level=level))
            area_scores.append(score)

        listening, reading = area_scores
        areas.append(AreaResult(
            area=area, score=float(listening + reading), maxScore=200, level=aggregate_level(listening, reading)
        ))

    return DiagnosticResults(formId=form.id, isSynthetic=form.is_synthetic, parts=parts, areas=areas)


def _score_level(percent: Decimal) -> Level:
    if percent >= 70:
        return "advanced"
    if percent >= 40:
        return "intermediate"
    return "beginner"


def _criterion_level(counts: dict[Tier, AssessmentTierCount]) -> Level:
    if counts["beginner"].correct == 0:
        return "below_beginner"
    if counts["intermediate"].correct < 2:
        return "beginner"
    if counts["advanced"].correct < 2:
        return "intermediate"
    return "advanced"


def _score(
    items: list[DiagnosticItem], correct_ids: set[str]
) -> tuple[Decimal, Decimal, Decimal]:
    maximum = sum((item.max_points for item in items), Decimal(0))
    earned = sum(
        (item.max_points for item in items if item.display.id in correct_ids), Decimal(0)
    )
    return earned, maximum, 100 * earned / maximum


def _grade_assessment(
    form: DiagnosticForm, answers: Mapping[str, str | None]
) -> DiagnosticResults:
    correct_ids = {
        item.display.id for item in form.questions
        if answers[item.display.id] == item.correct_option_id
    }
    by_skill: dict[Skill, list[DiagnosticItem]] = defaultdict(list)
    by_area: dict[Area, list[DiagnosticItem]] = defaultdict(list)
    for item in form.questions:
        by_area[item.display.area].append(item)
        by_skill[item.display.skill].append(item)

    skills: dict[Skill, AssessmentSkillResult] = {}
    skill_percent: dict[Skill, Decimal] = {}
    for skill in SKILLS:
        earned, maximum, percent = _score(by_skill[skill], correct_ids)
        skill_percent[skill] = percent
        skills[skill] = AssessmentSkillResult(
            score=float(earned), maxScore=float(maximum), percent=float(percent),
            level=_score_level(percent),
        )
    listening, reading = skill_percent["listening"], skill_percent["reading"]
    overall = AssessmentOverallResult(
        score=float(listening + reading), maxScore=200,
        level="below_beginner" if max(listening, reading) < 30 else aggregate_level(listening, reading),
    )

    parts: list[PartResult] = []
    areas: list[AreaResult] = []
    subjects: list[AssessmentSubjectResult] = []
    area_scores = {area: _score(by_area[area], correct_ids) for area in AREAS}
    for area in AREAS:
        items = by_area[area]
        for skill in SKILLS:
            part_items = [item for item in items if item.display.skill == skill]
            _, _, percent = _score(part_items, correct_ids)
            parts.append(PartResult(
                area=area, skill=skill, score=float(percent), maxScore=100, level=_score_level(percent),
            ))

        earned, maximum, percent = area_scores[area]
        area_level: Level
        if area == "daily":
            area_level = "intermediate" if percent >= 40 else "beginner"
        else:
            counts = {
                tier: AssessmentTierCount(
                    correct=sum(item.tier == tier and item.display.id in correct_ids for item in items),
                    total=sum(item.tier == tier for item in items),
                )
                for tier in TIERS
            }
            score_level = _score_level(percent)
            criterion_level = _criterion_level(counts)
            area_level = min((score_level, criterion_level), key=LEVEL_ORDER.index)
            subjects.append(AssessmentSubjectResult(
                area=area, score=float(earned), maxScore=float(maximum), percent=float(percent),
                scoreLevel=score_level, criterionLevel=criterion_level, level=area_level, tierCounts=counts,
            ))
        areas.append(AreaResult(area=area, score=float(2 * percent), maxScore=200, level=area_level))

    earned, maximum, percent = area_scores["daily"]
    communication = AssessmentCommunicationResult(
        score=float(earned), maxScore=float(maximum), percent=float(percent),
        questionCount=len(by_area["daily"]), status="adequate" if percent >= 40 else "needs_improvement",
    )
    summary = AssessmentSummary(
        gradeBand=form.grade_band, listening=skills["listening"], reading=skills["reading"],
        overall=overall, subjects=subjects, communication=communication,
    )
    return DiagnosticResults(
        formId=form.id, isSynthetic=form.is_synthetic, parts=parts, areas=areas, assessmentSummary=summary,
    )
