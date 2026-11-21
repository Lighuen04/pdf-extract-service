
FROM python:3.14-slim AS builder

WORKDIR /app
RUN pip install --no-cache-dir uv==0.11.2

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-cache --no-install-project
COPY app/ ./app/
RUN uv sync --locked --no-dev --no-cache --no-editable

FROM python:3.14-slim AS runtime

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-eng tesseract-ocr-spa zlib1g \
    && /usr/local/bin/python -m pip uninstall --yes pip \
    && rm -rf /usr/local/lib/python3.14/ensurepip /var/lib/apt/lists/* \
    && addgroup --system appgroup \
    && adduser --system --ingroup appgroup --home /home/appuser appuser

COPY --from=builder /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OMP_THREAD_LIMIT=1

USER appuser
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os; from urllib.request import urlopen; urlopen('http://127.0.0.1:' + os.getenv('PORT', '8000') + '/health/live', timeout=2).close()"

LABEL org.opencontainers.image.title="PDF Extract Service" \
      org.opencontainers.image.description="Extracción de texto y Markdown a partir de PDFs" \
      org.opencontainers.image.version="0.1.0" \
      org.opencontainers.image.source="https://github.com/Lighuen04/pdf-extract-service" \
      org.opencontainers.image.licenses="MIT"

CMD ["python", "-m", "app.server"]
