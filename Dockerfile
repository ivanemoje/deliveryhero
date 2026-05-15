FROM python:3.12-slim

WORKDIR /app

# System deps for pdfplumber (uses pdfminer under the hood)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpoppler-cpp-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

# Default: run the agent CLI
CMD ["python", "-m", "src.agent.pipeline"]
