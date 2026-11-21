"""Load the attached Korean assessment without publishing its answer data."""

import json
from pathlib import Path

from .dto import GradeBand, QuestionDisplay
from .forms import SKILLS, DiagnosticForm, DiagnosticItem


ASSESSMENT_DATA = Path(__file__).with_name("data") / "korean_assessment.json"
GRADE_BANDS: tuple[GradeBand, ...] = ("g34", "g56")
SECTION_COUNTS = {"listening": 15, "reading": 20}


def load_assessment_forms() -> dict[GradeBand, DiagnosticForm]:
    with ASSESSMENT_DATA.open(encoding="utf-8") as document:
        bank = json.load(document)
    if set(bank) != set(GRADE_BANDS):
        raise ValueError("Korean assessment expects both g34 and g56")

    forms = {}
    for band in GRADE_BANDS:
        questions = []
        for skill in SKILLS:
            section = bank[band][skill]
            if len(section) != SECTION_COUNTS[skill]:
                raise ValueError(f"Unexpected {skill} question count for {band}")
            for number, raw_item in enumerate(section, 1):
                item = raw_item.copy()
                original_points = item.pop("points")
                tier = item.pop("tier")
                answer = item.pop("correctOptionId")
                item.setdefault("audioUrl", None)
                item.setdefault("stimulusText", "")
                questions.append(DiagnosticItem(
                    display=QuestionDisplay(
                        id=f"{band}-{skill[0]}{number:02}",
                        sequence=len(questions) + 1,
                        skill=skill,
                        **item,
                    ),
                    correct_option_id=answer,
                    tier=tier,
                    max_points=original_points,
                ))
        forms[band] = DiagnosticForm(
            id=f"official-{band}-v1", rubric="korean-assessment-v1",
            grade_band=band, is_synthetic=False, questions=questions,
        )
    return forms
