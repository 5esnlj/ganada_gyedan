from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

Area = Literal["korean", "math", "social", "science", "daily"]
Role = Literal["teacher", "student"]
Kind = Literal["true_false", "multiple_choice_4"]
Level = Literal["below_beginner", "beginner", "intermediate", "advanced"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Option(Input):
    id: str = Field(min_length=1, max_length=32)
    text: str = Field(min_length=1, max_length=1000)


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    nextCursor: str | None = None
