# Production image for the Shadow Mode LLM Evaluator.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code only (see .dockerignore for exclusions).
COPY app ./app

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser
USER appuser

# App Platform / most PaaS inject $PORT; default to 8080 locally.
ENV PORT=8080
EXPOSE 8080

# Divergence traces go to a writable path (the image FS is ephemeral anyway).
ENV SQLITE_DB_PATH=/tmp/shadow_traces.db

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
