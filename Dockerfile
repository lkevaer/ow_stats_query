FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1 \
    OW_BINDINGS_FILE=/app/api_server/data/bindings.json \
    OW_OCR_MODEL_DIR=/opt/ow-ocr-models
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
COPY requirements_for_api.txt constraints-api.txt ./
RUN pip install --no-cache-dir -r requirements_for_api.txt
COPY . .
RUN mkdir -p /app/data /app/api_server/data
RUN python -m api_server.batch_rating.ocr_engine
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/ready', timeout=4)" || exit 1
CMD ["python", "-m", "uvicorn", "api_server.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--loop", "asyncio", "--limit-concurrency", "64", "--limit-max-requests", "5000", "--timeout-graceful-shutdown", "30"]
