from typing import Literal

from pydantic import BaseModel, Field, SecretStr, model_validator

from app.core.dto import Input

JobState = Literal["queued", "running", "succeeded", "failed", "unknown"]


class PdfBlock(Input):
    id: str = Field(pattern=r"^p[1-9][0-9]*b[1-9][0-9]*$", max_length=32)
    text: str = Field(min_length=1, max_length=10_000, pattern=r"\S")
    preserve: bool = False


class PdfPage(Input):
    number: int = Field(ge=1, le=100)
    blocks: list[PdfBlock] = Field(max_length=200)


class Generate(Input):
    sourceId: str = Field(min_length=1, max_length=80)
    studentMemberIds: list[str] = Field(min_length=1, max_length=30)
    operation: Literal["convert"] = "convert"
    pages: list[PdfPage] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def validate_pages(self):
        if len(set(self.studentMemberIds)) != len(self.studentMemberIds):
            raise ValueError("같은 학생을 중복 선택할 수 없습니다.")
        first = self.pages[0].number
        if [p.number for p in self.pages] != list(range(first, first + len(self.pages))):
            raise ValueError("연속된 최대 10쪽을 선택해 주세요.")
        blocks = [b for page in self.pages for b in page.blocks]
        if not blocks:
            raise ValueError("선택한 쪽에서 PDF 본문을 읽을 수 없습니다.")
        if len(blocks) > 500 or sum(len(b.text) for b in blocks) > 60_000:
            raise ValueError("한 번에 처리할 PDF 범위를 줄여 주세요.")
        ids = [b.id for b in blocks]
        if len(set(ids)) != len(ids) or any(
            not b.id.startswith(f"p{page.number}b") for page in self.pages for b in page.blocks
        ):
            raise ValueError("PDF 문단과 쪽 번호가 일치하지 않습니다.")
        return self


class CredentialCreate(Input):
    label: str = Field(default="Solar", min_length=1, max_length=100, pattern=r"\S")
    apiKey: SecretStr = Field(min_length=8, max_length=500)


class CredentialOut(BaseModel):
    id: str
    teacherId: str
    label: str
    maskedSuffix: str
    status: Literal["pending", "active"]
    provider: Literal["solar"] = "solar"


class JobOut(BaseModel):
    id: str
    classId: str
    sourceId: str
    studentMemberId: str
    credentialId: str
    operation: Literal["convert"] = "convert"
    status: JobState
    createdBy: str
    resultId: str | None = None
    resultType: Literal["material"] | None = None
    errorCode: str | None = None
    errorMessage: str | None = None
