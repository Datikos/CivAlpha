FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY civalpha_ml ./civalpha_ml
RUN useradd --system --uid 10001 civalpha && mkdir -p /data && chown civalpha /data
USER civalpha
EXPOSE 8000
CMD ["uvicorn", "civalpha_ml.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
