FROM python:3.12-slim

WORKDIR /app

# Includes system dependencies for PDF parsing and OCR fallback.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpoppler-cpp-dev \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

CMD ["python", "-m", "src.agent.pipeline"]
