import fitz
import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.mark.parametrize("multipart", [False, True])
@pytest.mark.parametrize("path", ["/extract", "/extract/text"])
def test_upload_contract(build_pdf, multipart, path):
    pdf = build_pdf([("Titulo", 24), ("Cuerpo", 11), ("Otro", 11)])
    kwargs = (
        {"files": {"file": ("documento.pdf", pdf, "application/pdf")}}
        if multipart
        else {"content": pdf, "headers": {"Content-Type": "application/pdf"}}
    )
    with TestClient(app) as client:
        response = client.post(path, **kwargs)
    assert response.status_code == 200
    content = "# Titulo\n\nCuerpo\n\nOtro" if path == "/extract" else "Titulo\nCuerpo\nOtro"
    assert response.json() == {"content": content, "page_count": 1}


@pytest.mark.anyio
@pytest.mark.parametrize("multipart", [False, True])
async def test_stream_stops_at_size_limit_without_content_length(monkeypatch, multipart):
    monkeypatch.setenv("APP_MAX_PDF_SIZE_BYTES", "64")
    headers = {"Content-Type": "application/pdf"}
    if multipart:
        headers["Content-Type"] = "multipart/form-data; boundary=pdf-boundary"

    async def chunks():
        if multipart:
            yield (
                b'--pdf-boundary\r\nContent-Disposition: form-data; name="file"; filename="a.pdf"\r\n'
                b"Content-Type: application/pdf\r\n\r\n"
            )
        yield b"%PDF-" + b"0" * 60
        yield b"1"
        pytest.fail("El endpoint leyó más allá del límite")

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        response = await client.post("/extract", headers=headers, content=chunks())
    assert response.status_code == 413


@pytest.mark.parametrize("pdf", [b"", b"not a PDF", b"%PDF-1.7\n%%EOF"])
def test_invalid_payload_returns_400(pdf):
    with TestClient(app) as client:
        response = client.post("/extract/text", content=pdf)
    assert response.status_code == 400
    assert set(response.json()) == {"detail"}


def test_truncated_multipart_returns_400(pdf_bytes):
    body = b'--abc\r\nContent-Disposition: form-data; name="file"; filename="a.pdf"\r\n\r\n'
    with TestClient(app) as client:
        response = client.post(
            "/extract",
            headers={"Content-Type": "multipart/form-data; boundary=abc"},
            content=body + pdf_bytes,
        )
    assert response.status_code == 400


def test_encrypted_pdf_returns_public_error_and_releases_capacity(monkeypatch):
    monkeypatch.setenv("APP_MAX_PENDING_EXTRACTIONS", "1")
    with fitz.open() as doc:
        doc.new_page()
        pdf = doc.write(
            encryption=fitz.PDF_ENCRYPT_AES_256,
            owner_pw="owner-password",
            user_pw="user-password",
        )

    with TestClient(app) as client:
        response = client.post(
            "/extract/text", content=pdf, headers={"Content-Type": "application/pdf"}
        )
        assert response.status_code == 400
        assert response.json() == {"detail": "El contenido no corresponde a un PDF valido."}
        assert client.get("/health/ready").status_code == 200
