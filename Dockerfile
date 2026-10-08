FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./src/
COPY web/ ./web/
RUN useradd -u 10001 -m appuser && mkdir -p /data && chown appuser:appuser /data
USER appuser
EXPOSE 10000
# Render provides PORT=10000 by default. Docker Compose can override it to 8000.
CMD ["sh", "-c", "exec uvicorn src.app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers"]