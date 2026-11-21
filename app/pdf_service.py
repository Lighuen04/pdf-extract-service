from __future__ import annotations

import io
import logging
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager

import fitz
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class InvalidPDFError(ValueError):
    """Excepción lanzada cuando el contenido no es un PDF válido."""

    pass


INVALID_PDF_CONTENT_ERROR = "El contenido no corresponde a un PDF valido."


class OCRError(Exception):
    """OCR was requested but recognition failed."""


class ExtractionResult(BaseModel):
    """Respuesta estándar de extracción — compatible con contrato original."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(description="Texto extraído (plano o Markdown)")
    page_count: int = Field(ge=0, description="Número de páginas del PDF")


H1_SIZE_RATIO = 1.5
H2_SIZE_RATIO = 1.2


def _font_sizes(block: dict, counts: Counter[float]) -> float:
    largest = 0.0
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            text = span.get("text", "").strip()
            if text:
                size = round(span["size"], 1)
                counts[size] += len(text)
                largest = max(largest, size)
    return largest


def _to_markdown(text: str, largest: float, body_size: float) -> str:
    ratio = largest / body_size if body_size > 0 else 0
    if ratio >= H1_SIZE_RATIO:
        return "# " + text.replace("\n", " ")
    if ratio >= H2_SIZE_RATIO:
        return "## " + text.replace("\n", " ")
    return text


def extract_text_from_image_bytes(image_bytes: bytes | None, languages: str = "eng+spa") -> str:
    """Return recognized text, or raise OCRError if recognition could not run."""
    if not image_bytes:
        return ""

    try:
        import pytesseract
        from PIL import Image

        with Image.open(io.BytesIO(image_bytes)) as image:
            text: str = pytesseract.image_to_string(image, lang=languages, timeout=10)
        return text.strip()
    except Exception as exc:
        raise OCRError("Falló el OCR de un bloque de imagen.") from exc


TEXT_BLOCK = 0
IMAGE_BLOCK = 1
TEXT_ONLY_FLAGS = fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES


@contextmanager
def _open_pdf(file_bytes: bytes) -> Iterator[fitz.Document]:
    if not file_bytes.startswith(b"%PDF-"):
        raise InvalidPDFError(INVALID_PDF_CONTENT_ERROR)

    try:
        with fitz.open(stream=file_bytes, filetype="pdf") as doc:
            if not doc.is_pdf or doc.needs_pass or doc.page_count < 1:
                raise InvalidPDFError(INVALID_PDF_CONTENT_ERROR)
            yield doc
    except InvalidPDFError:
        raise
    except (RuntimeError, ValueError) as exc:
        raise InvalidPDFError(INVALID_PDF_CONTENT_ERROR) from exc


def _blocks(doc: fitz.Document, ocr_enabled: bool) -> Iterator[dict]:
    flags = fitz.TEXTFLAGS_DICT if ocr_enabled else TEXT_ONLY_FLAGS
    for page in doc:
        yield from page.get_text("dict", flags=flags, sort=True).get("blocks", [])


def _block_text(block: dict, ocr_enabled: bool, languages: str) -> str:
    block_type = block.get("type")
    if block_type == TEXT_BLOCK:
        lines = (
            "".join(span.get("text", "") for span in line.get("spans", [])).strip()
            for line in block.get("lines", [])
        )
        return "\n".join(line for line in lines if line)
    if block_type == IMAGE_BLOCK and ocr_enabled:
        return extract_text_from_image_bytes(block.get("image"), languages)
    return ""


def _extract(
    file_bytes: bytes, *, ocr_enabled: bool, ocr_languages: str, markdown: bool
) -> ExtractionResult:
    parts: list[str] = []
    headings: list[tuple[str, float]] = []
    font_counts: Counter[float] = Counter()
    ocr_failed = False
    with _open_pdf(file_bytes) as doc:
        for block in _blocks(doc, ocr_enabled):
            largest = _font_sizes(block, font_counts) if markdown else 0.0
            try:
                text = _block_text(block, ocr_enabled, ocr_languages)
            except OCRError:
                logger.warning("OCR failed for an image block")
                ocr_failed = True
                continue
            if text:
                if markdown:
                    headings.append((text, largest))
                else:
                    parts.append(text)

        if not (parts or headings) and ocr_failed:
            raise OCRError("No se pudo completar el OCR.")
        if markdown:
            most_common = font_counts.most_common(1)
            body_size = most_common[0][0] if most_common else 0.0
            content = "\n\n".join(_to_markdown(text, size, body_size) for text, size in headings)
        else:
            content = "\n".join(parts)
        return ExtractionResult(content=content, page_count=doc.page_count)


def extract_text_from_pdf_bytes(
    file_bytes: bytes, *, ocr_enabled: bool, ocr_languages: str = "eng+spa"
) -> ExtractionResult:
    return _extract(
        file_bytes, ocr_enabled=ocr_enabled, ocr_languages=ocr_languages, markdown=False
    )


def extract_markdown_from_pdf_bytes(
    file_bytes: bytes, *, ocr_enabled: bool, ocr_languages: str = "eng+spa"
) -> ExtractionResult:
    return _extract(file_bytes, ocr_enabled=ocr_enabled, ocr_languages=ocr_languages, markdown=True)
