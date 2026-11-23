import shutil

import fitz
import pytest
import pytesseract
from PIL import Image, ImageDraw, ImageFont
from fastapi.testclient import TestClient

from app.settings import get_settings
from app.main import app
from app.pdf_service import extract_text_from_pdf_bytes


@pytest.mark.parametrize("value", ["eng,spa", "eng+spa", " eng, + spa "])
def test_normalize_ocr_languages(monkeypatch, value):
    monkeypatch.setenv("APP_OCR_LANGUAGES", value)
    assert get_settings().ocr_languages == "eng+spa"


@pytest.mark.parametrize("endpoint", ["/extract", "/extract/text"])
def test_ocr_failure_preserves_native_text_and_surfaces_when_no_text(
    monkeypatch, pdf_with_image, build_pdf, endpoint
):
    monkeypatch.setenv("APP_OCR_ENABLED", "true")

    def fail(*args, **kwargs):
        raise RuntimeError("OCR unavailable")

    monkeypatch.setattr(pytesseract, "image_to_string", fail)
    with fitz.open(stream=pdf_with_image, filetype="pdf") as doc:
        for rect in doc[0].search_for("Texto normal"):
            doc[0].add_redact_annot(rect)
        doc[0].apply_redactions(images=0)
        image_only = doc.write()

    with TestClient(app) as client:
        partial = client.post(endpoint, content=pdf_with_image)
        assert partial.status_code == 200
        assert partial.json() == {"content": "Texto normal", "page_count": 1}
        failed = client.post(endpoint, content=image_only)
        assert failed.status_code == 500
        assert failed.json() == {"detail": "No se pudo completar el OCR."}
        assert client.post(endpoint, content=build_pdf([])).json()["content"] == ""


@pytest.mark.integration
@pytest.mark.parametrize("languages", ["eng", "eng+spa"])
def test_tesseract_extracts_image_only_pdf(languages):
    if not shutil.which(pytesseract.pytesseract.tesseract_cmd):
        pytest.skip("Tesseract no está disponible en PATH")
    if not set(languages.split("+")) <= set(pytesseract.get_languages()):
        pytest.skip(f"Tesseract requiere {languages} para esta prueba")

    image = Image.new("RGB", (650, 160), "white")
    ImageDraw.Draw(image).text(
        (20, 30),
        "HELLO WORLD",
        font=ImageFont.load_default(size=62),
        fill="black",
    )
    with fitz.open() as doc:
        page = doc.new_page(width=image.width, height=image.height)
        pixmap = fitz.Pixmap(fitz.csRGB, image.width, image.height, image.tobytes(), False)
        page.insert_image(page.rect, pixmap=pixmap)
        pdf = doc.write()

    assert extract_text_from_pdf_bytes(pdf, ocr_enabled=False).content == ""
    assert (
        "HELLO WORLD"
        in extract_text_from_pdf_bytes(pdf, ocr_enabled=True, ocr_languages=languages).content
    )
