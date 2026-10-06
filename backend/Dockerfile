# Build context: the repository root (docker-compose.yml), so the image also carries db/migration.
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 CIVALPHA_MIGRATIONS_DIR=/app/db/migration
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# docker compose build --build-arg GIT_COMMIT=$(git rev-parse HEAD) api: recorded in the trial registry with every backtest.
# Declared after the dependency layer so a new commit hash does not reinstall the requirements.
ARG GIT_COMMIT=unknown
ENV CIVALPHA_GIT_COMMIT=$GIT_COMMIT
COPY backend/civalpha ./civalpha
COPY db/migration ./db/migration
RUN useradd --system --uid 10001 civalpha && mkdir -p /data && chown civalpha /data
USER civalpha
EXPOSE 8000
CMD ["uvicorn", "civalpha.platform.app:app", "--host", "0.0.0.0", "--port", "8000"]
