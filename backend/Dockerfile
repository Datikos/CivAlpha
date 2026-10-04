# Build context: the repository root (docker-compose.yml), so the image also carries db/migration.
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 CIVALPHA_MIGRATIONS_DIR=/app/db/migration
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/civalpha ./civalpha
COPY db/migration ./db/migration
RUN useradd --system --uid 10001 civalpha && mkdir -p /data && chown civalpha /data
USER civalpha
EXPOSE 8000
CMD ["uvicorn", "civalpha.platform.app:app", "--host", "0.0.0.0", "--port", "8000"]
