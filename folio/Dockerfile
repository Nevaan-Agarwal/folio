FROM python:3.11-slim

# Install Tesseract OCR binary + language packs required by the app.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-deu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render injects PORT at runtime; default to 10000 for local container runs.
ENV PORT=10000
ENV TESSERACT_CMD=/usr/bin/tesseract

CMD ["sh", "-c", "gunicorn \"app:create_app()\" --bind 0.0.0.0:${PORT}"]
