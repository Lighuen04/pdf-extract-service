import asyncio
from threading import Event

import httpx
import pytest

from app.main import app
from app.pdf_service import ExtractionResult

pytestmark = pytest.mark.anyio


@pytest.fixture
def blocked_worker(monkeypatch):
    started, release = Event(), Event()

    def extract(*args, **kwargs):
        started.set()
        assert release.wait(8)
        return ExtractionResult(content="texto", page_count=1)

    monkeypatch.setattr("app.main.extract_text_from_pdf_bytes", extract)
    monkeypatch.setenv("APP_EXTRACT_WORKERS", "1")
    monkeypatch.setenv("APP_MAX_PENDING_EXTRACTIONS", "1")
    yield started, release
    release.set()


def client_for_app():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def wait_until(check):
    async with asyncio.timeout(4):
        while not check():
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("cancel_request", [False, True])
async def test_abandoned_request_keeps_running_job_counted(
    monkeypatch, blocked_worker, pdf_bytes, cancel_request
):
    monkeypatch.setenv("APP_EXTRACTION_TIMEOUT_SECONDS", "1")
    started, release = blocked_worker
    async with app.router.lifespan_context(app), client_for_app() as client:
        request = asyncio.create_task(client.post("/extract/text", content=pdf_bytes))
        await wait_until(started.is_set)
        if cancel_request:
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
        else:
            assert (await request).status_code == 504

        try:
            assert (await client.get("/health/ready")).status_code == 503
            rejected = await client.post("/extract/text", content=pdf_bytes)
            assert rejected.status_code == 503
            assert rejected.headers["Retry-After"] == "1"
        finally:
            release.set()
        await wait_until(lambda: app.state.extraction_pool.get_stats().pending == 0)
        assert (await client.get("/health/ready")).status_code == 200
        assert (await client.post("/extract/text", content=pdf_bytes)).status_code == 200


@pytest.mark.parametrize("release_during_shutdown", [True, False])
async def test_lifespan_drains_or_returns_at_deadline(
    monkeypatch, blocked_worker, pdf_bytes, release_during_shutdown
):
    monkeypatch.setenv("APP_SHUTDOWN_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("APP_MAX_PENDING_EXTRACTIONS", "2")
    started, release = blocked_worker
    lifespan = app.router.lifespan_context(app)
    await lifespan.__aenter__()
    async with client_for_app() as client:
        active = asyncio.create_task(client.post("/extract/text", content=pdf_bytes))
        await wait_until(started.is_set)
        service = app.state.extraction_pool
        queued = asyncio.create_task(client.post("/extract/text", content=pdf_bytes))
        await wait_until(lambda: service.get_stats().pending == 2)
        closing = asyncio.create_task(lifespan.__aexit__(None, None, None))
        try:
            await wait_until(lambda: service.get_stats().closing)
            ready = await client.get("/health/ready")
            assert ready.status_code == 503
            assert ready.json()["status"] == "closing"
            assert (await client.get("/health/live")).status_code == 200
            assert not closing.done()
            assert (await asyncio.wait_for(queued, 0.5)).status_code == 503
            if release_during_shutdown:
                release.set()
            await asyncio.wait_for(closing, 3)
            assert service.get_stats().pending == (0 if release_during_shutdown else 1)
        finally:
            release.set()
            await closing
            await asyncio.gather(active, queued, return_exceptions=True)


async def test_uploads_share_admission_before_the_body_is_read(monkeypatch, pdf_bytes):
    monkeypatch.setenv("APP_MAX_PENDING_EXTRACTIONS", "1")
    reading, release = asyncio.Event(), asyncio.Event()

    async def waiting_upload():
        reading.set()
        await release.wait()
        yield pdf_bytes

    async def rejected_upload():
        pytest.fail("The saturated endpoint started reading another PDF")
        yield b""

    async with app.router.lifespan_context(app), client_for_app() as client:
        first = asyncio.create_task(client.post("/extract/text", content=waiting_upload()))
        try:
            await asyncio.wait_for(reading.wait(), 3)
            rejected = await client.post("/extract/text", content=rejected_upload())
            assert rejected.status_code == 503
            assert rejected.headers["Retry-After"] == "1"
            assert (await client.get("/health/ready")).json()["pending_extractions"] == 1
        finally:
            release.set()
        assert (await first).status_code == 200


@pytest.mark.parametrize("multipart", [False, True])
async def test_upload_timeout_releases_admission_without_starting_extraction(
    monkeypatch, pdf_bytes, multipart
):
    monkeypatch.setenv("APP_MAX_PENDING_EXTRACTIONS", "1")
    monkeypatch.setenv("APP_UPLOAD_TIMEOUT_SECONDS", "1")
    calls = []

    def extract(data, **kwargs):
        calls.append(data)
        return ExtractionResult(content="texto", page_count=1)

    monkeypatch.setattr("app.main.extract_text_from_pdf_bytes", extract)

    async def incomplete_upload():
        if multipart:
            yield (
                b'--pdf\r\nContent-Disposition: form-data; name="file"; filename="test.pdf"'
                b"\r\n\r\n" + pdf_bytes
            )
        else:
            yield pdf_bytes
        await asyncio.Event().wait()

    media_type = "multipart/form-data; boundary=pdf" if multipart else "application/pdf"
    async with app.router.lifespan_context(app), client_for_app() as client:
        async with asyncio.timeout(4):
            response = await client.post(
                "/extract/text", content=incomplete_upload(), headers={"Content-Type": media_type}
            )
        assert response.status_code == 408
        assert not calls
        ready = await client.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json()["pending_extractions"] == 0
        assert (await client.post("/extract/text", content=pdf_bytes)).status_code == 200
        assert calls == [pdf_bytes]
