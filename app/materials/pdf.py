"""Extract text from source PDFs for the local development workflow."""

import re
from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from app.core.errors import fail

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_PAGES = 100
MAX_TEXT_LENGTH = 250_000


def extract_source_text(data: bytes) -> tuple[str, list[dict[str, str]]]:
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            fail(422, "PDF_ENCRYPTED", "암호가 없는 PDF 파일을 올려 주세요.")
        if len(reader.pages) > MAX_PAGES:
            fail(422, "PDF_EXTRACTION_LIMIT", f"PDF는 최대 {MAX_PAGES}페이지까지 읽을 수 있습니다.")

        page_text = []
        text_length = 0
        for page in reader.pages:
            text = page.extract_text().replace("\x00", "").strip()
            if text:
                text_length += len(text)
                if text_length > MAX_TEXT_LENGTH:
                    fail(422, "PDF_EXTRACTION_LIMIT", "PDF 본문이 추출 한도를 초과했습니다.")
                page_text.append(text)
    except (PyPdfError, ValueError, TypeError, KeyError, IndexError, OSError, RuntimeError):
        fail(422, "PDF_UNREADABLE", "PDF 본문을 읽을 수 없습니다.")

    source_text = "\n\n".join(page_text)
    if not source_text:
        fail(422, "PDF_TEXT_REQUIRED", "텍스트를 선택할 수 있는 PDF 파일을 올려 주세요.")
    return source_text, source_sentences(source_text)


def source_sentences(source_text: str) -> list[dict[str, str]]:
    segments = [
        segment.strip()
        for segment in re.split(r"[\r\n]+|(?<=[.!?。！？])[^\S\r\n]+", source_text)
        if segment.strip()
    ]
    return [
        {"id": f"s{position}", "text": text}
        for position, text in enumerate(segments, 1)
    ]
