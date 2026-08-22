# Multi-stage build. Stage 1 has compilers and build tooling; stage 2 has only
# the installed packages and your code. This typically cuts image size by 60-80%,
# which directly reduces Cloud Run cold-start time -- the image must be pulled
# before the first request is served.

FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv && /opt/venv/bin/pip install -r requirements.txt

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# Non-root. If an injection attack ever reaches code execution, this is the
# difference between a contained blast radius and an uncontained one.
RUN useradd --create-home --uid 1000 appuser
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY --chown=appuser:appuser app ./app
USER appuser

# No secrets in the image. Cloud Run injects them from Secret Manager at runtime.
# Cloud Run sets $PORT; binding a hardcoded port is the #1 first-deploy failure.
EXPOSE 8080
CMD exec uvicorn app.api.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1