FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080 \
    TESSERACT_CMD=/usr/bin/tesseract \
    FLASK_ENV=production

# Install Tesseract OCR binary + language packs required by the app.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-deu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /tmp/folio-storage \
    && chown -R appuser:appuser /app /tmp/folio-storage

USER appuser

CMD ["sh", "-c", "gunicorn \"app:create_app()\" --bind 0.0.0.0:${PORT}"]
