FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /build
COPY requirements-docker.txt requirements.txt
RUN python -m venv /opt/venv \
    && grep -v -E "^(torch|nvidia-|triton)" requirements.txt > requirements-notorch.txt \
    && /opt/venv/bin/pip install --no-deps -r requirements-notorch.txt \
    && /opt/venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu \
    && /opt/venv/bin/pip check || true

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH"
RUN useradd --create-home --uid 1000 appuser
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY --chown=appuser:appuser app ./app
USER appuser
EXPOSE 8080
CMD exec uvicorn app.api.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1
