from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.dto import Area

from .dto import GradeBand, QuestionDisplay, Skill, Tier


AREAS: tuple[Area, ...] = ("korean", "math", "social", "science", "daily")
SKILLS: tuple[Skill, ...] = ("listening", "reading")
TIERS: tuple[Tier, ...] = ("beginner", "intermediate", "advanced")


class DiagnosticItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    display: QuestionDisplay
    correct_option_id: str
    tier: Tier
    gate_slot: Literal[1, 2] | None = None
    max_points: Decimal = Field(gt=0, le=100, decimal_places=2)

    @model_validator(mode="after")
    def validate_options(self) -> "DiagnosticItem":
        option_ids = [option.id for option in self.display.options]
        if len(set(option_ids)) != len(option_ids):
            raise ValueError("Duplicate option IDs are not allowed")
        if self.correct_option_id not in option_ids:
            raise ValueError("The correct option must be among the choices")
        return self


class DiagnosticForm(BaseModel):
    """Immutable published form with a rubric fixed for the lifetime of an attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=100)
    rubric: Literal["dml-v1", "korean-assessment-v1"] = "dml-v1"
    grade_band: GradeBand | None = None
    is_synthetic: bool
    questions: tuple[DiagnosticItem, ...]

    @model_validator(mode="after")
    def validate_form(self) -> "DiagnosticForm":
        if len({item.display.sequence for item in self.questions}) != len(self.questions):
            raise ValueError("Duplicate question sequences are not allowed")
        if len({item.display.id for item in self.questions}) != len(self.questions):
            raise ValueError("Duplicate question IDs are not allowed")

        groups: dict[tuple[Area, Skill], list[DiagnosticItem]] = defaultdict(list)
        for item in self.questions:
            groups[item.display.area, item.display.skill].append(item)
        if self.rubric == "korean-assessment-v1":
            if self.grade_band is None:
                raise ValueError("Korean assessment needs a grade band")
            if any(not groups[area, skill] for area in AREAS for skill in SKILLS):
                raise ValueError("Korean assessment needs questions for every area and skill")
            return self

        if self.grade_band is not None:
            raise ValueError("DML v1 does not use a grade band")
        for area in AREAS:
            for skill in SKILLS:
                part = groups[area, skill]
                if sum(item.max_points for item in part) != 100:
                    raise ValueError(f"{area}/{skill} must total 100 points")
                for tier in TIERS:
                    slots = sorted(
                        item.gate_slot
                        for item in part
                        if item.tier == tier and item.gate_slot is not None
                    )
                    if slots != [1, 2]:
                        raise ValueError(f"{area}/{skill}/{tier} needs gate slots 1 and 2")
        return self


class FormProvider(Protocol):
    def active_form(self) -> DiagnosticForm | None: ...


@dataclass(frozen=True)
class StaticFormProvider:
    form: DiagnosticForm | None

    def active_form(self) -> DiagnosticForm | None:
        return self.form
