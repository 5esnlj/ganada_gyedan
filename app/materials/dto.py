"""Wire DTOs for private files, source materials and student editions."""

from typing import Annotated, Literal

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
    fileId: str | None
    title: str
    subject: Area
    unit: str
    unitNumber: str | None = None
    thumbnailUrl: str | None = None
    sourceText: str
    sentences: list[SourceSentence]
    createdAt: str


class PhraseChange(Input):
    start: int = Field(ge=0, lt=10000, description="Code point offset in the converted paragraph.")
    original: str = Field(min_length=2, max_length=14, pattern=r"\S")
    replacement: str = Field(min_length=1, max_length=200, pattern=r"\S")


class Paragraph(Input):
    id: str = Field(min_length=1, max_length=64, pattern=r"\S")
    text: str = Field(min_length=1, max_length=10000, pattern=r"\S")
    page: int | None = Field(default=None, ge=1, le=100)
    phraseChanges: list[PhraseChange] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def validate_phrase_offsets(self):
        end = 0
        for change in self.phraseChanges:
            next_end = change.start + len(change.replacement)
            if change.start < end or self.text[change.start:next_end] != change.replacement:
                raise ValueError("변환 표현의 위치가 문단 본문과 일치하지 않습니다.")
            end = next_end
        return self


class PdfInfo(BaseModel):
    pageCount: int


class StudyVisual(Input):
    kind: Literal["rel", "seq", "list"]
    title: str = Field(default="", max_length=200)
    center: str | None = Field(default=None, max_length=200)
    items: list[str] = Field(min_length=2, max_length=6)


class StudyNote(Input):
    blockId: str = Field(min_length=1, max_length=64)
    text: str | None = Field(default=None, max_length=10000)
    visual: StudyVisual | None = None


class Content(Input):
    paragraphs: list[Paragraph] = Field(min_length=1, max_length=500)
    translations: list[dict[str, str]] = Field(default_factory=list)
    omittedPages: list[int] = Field(default_factory=list, max_length=10)
    limitedSupportPages: list[Annotated[int, Field(ge=1, le=100)]] = Field(default_factory=list, max_length=10)
    hiddenBlockIds: list[str] = Field(default_factory=list, max_length=500)
    studyNotes: list[StudyNote] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def validate_paragraph_ids(self):
        visible = {item.id for item in self.paragraphs}
        if len(visible) != len(self.paragraphs):
            raise ValueError("문장 ID가 중복되었습니다.")
        if visible.intersection(self.hiddenBlockIds) or any(note.blockId not in visible for note in self.studyNotes):
            raise ValueError("문단 배치나 보조 설명이 일치하지 않습니다.")
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
    hasPdf: bool = False
    unitNumber: str | None = None
    thumbnailUrl: str | None = None
    originalContent: str | None = None
