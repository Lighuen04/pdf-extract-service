from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import __version__
from app.extraction_pool import ExtractionPool, ExtractionTimeoutError, ServiceOverloadedError
from app.logging_config import configure_logging
from app.pdf_service import (
    ExtractionResult,
    InvalidPDFError,
    OCRError,
    extract_markdown_from_pdf_bytes,
    extract_text_from_pdf_bytes,
)
from app.settings import Settings, get_settings
from app.uploads import read_pdf_payload

load_dotenv()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    pool = ExtractionPool(settings)
    app.state.settings = settings
    app.state.extraction_pool = pool
    try:
        yield
    finally:
        await pool.shutdown()


app = FastAPI(title=get_settings().app_name, version=__version__, lifespan=lifespan)


class ReadinessResponse(BaseModel):
    """Health check readiness."""

    status: str
    pending_extractions: int
    max_pending_extractions: int
    extraction_workers: int
    queue_utilization_percent: float
    closing: bool


@app.exception_handler(ServiceOverloadedError)
async def overloaded_handler(request: Request, exc: ServiceOverloadedError):
    return JSONResponse(status_code=503, content={"detail": str(exc)}, headers={"Retry-After": "1"})


@app.exception_handler(ExtractionTimeoutError)
async def timeout_handler(request: Request, exc: ExtractionTimeoutError):
    return JSONResponse(status_code=504, content={"detail": str(exc)})


@app.exception_handler(OCRError)
async def ocr_error_handler(request: Request, exc: OCRError):
    return JSONResponse(status_code=500, content={"detail": "No se pudo completar el OCR."})


@app.exception_handler(InvalidPDFError)
async def invalid_pdf_handler(request: Request, exc: InvalidPDFError):
    logger.warning("Invalid PDF: %s", exc)
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Error interno del servidor"},
    )


@app.get("/health")
@app.get("/health/live")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", responses={503: {"description": "Extraction unavailable"}})
async def readiness(request: Request, response: Response) -> ReadinessResponse:
    pool: ExtractionPool = request.app.state.extraction_pool
    stats = pool.get_stats()
    status = "ready"
    if not stats.available:
        response.status_code = 503
        status = (
            "closing"
            if stats.closing
            else "busy" if stats.pending >= stats.max_pending else "unavailable"
        )

    return ReadinessResponse(
        status=status,
        pending_extractions=stats.pending,
        max_pending_extractions=stats.max_pending,
        extraction_workers=stats.workers,
        queue_utilization_percent=round(stats.queue_utilization, 2),
        closing=stats.closing,
    )


async def _run_extraction(
    request: Request, extractor: Callable[..., ExtractionResult]
) -> ExtractionResult:
    settings: Settings = request.app.state.settings

    async def read_payload() -> bytes:
        timeout = asyncio.timeout(settings.upload_timeout_seconds)
        try:
            async with timeout:
                return await read_pdf_payload(request, settings.max_pdf_size_bytes)
        except TimeoutError as exc:
            if not timeout.expired():
                raise
            raise HTTPException(408, "Tiempo de recepción del PDF excedido.") from exc

    pool: ExtractionPool = request.app.state.extraction_pool
    return await pool.run(extractor, read_payload)


@app.post("/extract")
async def extract(request: Request) -> ExtractionResult:
    return await _run_extraction(request, extract_markdown_from_pdf_bytes)


@app.post("/extract/text")
async def extract_text(request: Request) -> ExtractionResult:
    return await _run_extraction(request, extract_text_from_pdf_bytes)
