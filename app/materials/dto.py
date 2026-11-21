"""Wire DTOs for private files, source materials and student editions."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.dto import Area, Input


class UploadOut(BaseModel):
    id: str
    classId: str
    createdBy: str
    subject: Area
    name: str
    size: int
    mimeType: Literal["application/pdf"] = "application/pdf"
    isSynthetic: bool = False
    extractionStatus: str
    notice: str


class SourceCreate(Input):
    model_config = ConfigDict(str_strip_whitespace=True)

    fileId: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=200)
    unit: str = Field(default="", max_length=150)


class SourceSentence(BaseModel):
    id: str
    text: str


class SourceOut(BaseModel):
    id: str
    classId: str
    createdBy: str
    fileId: str
    title: str
    subject: Area
    unit: str
    unitNumber: str | None = None
    thumbnailUrl: str | None = None
    sourceText: str
    sentences: list[SourceSentence]
    createdAt: str


class Paragraph(Input):
    id: str = Field(min_length=1, max_length=64, pattern=r"\S")
    text: str = Field(min_length=1, max_length=10000, pattern=r"\S")


class Content(Input):
    paragraphs: list[Paragraph] = Field(min_length=1, max_length=100)
    translations: list[dict[str, str]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_paragraph_ids(self):
        if len({item.id for item in self.paragraphs}) != len(self.paragraphs):
            raise ValueError("문장 ID가 중복되었습니다.")
        return self


class MaterialPatch(Input):
    content: Content


class MaterialOut(BaseModel):
    id: str
    classId: str
    sourceId: str
    studentMemberId: str
    subject: Area
    title: str
    unit: str
    status: Literal["draft", "reviewed", "published", "withdrawn"]
    version: int
    revision: int
    content: Content
    createdAt: str
    publishedAt: str | None
    reviewedAt: str | None
    isRecentPublished: bool
    unitNumber: str | None = None
    thumbnailUrl: str | None = None
    originalContent: str | None = None
