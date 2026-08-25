FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /build
COPY requirements.txt requirements.txt
# requirements-docker.txt (a copy of this file minus its header comment,
# D40) is gone -- confirmed the only diff between the two was that
# comment; no `-e .` line exists in the current export (that was D40's
# real reason for stripping), and pip ignores `#` lines regardless, so
# maintaining a second hash-pinned file to install from was pure drift
# risk with no functional purpose left.
#
# pyproject.toml's [tool.uv.sources]/[tool.uv.index] now pins torch to the
# CPU wheel index at LOCK time (torch==2.13.0+cpu, not the CUDA build) --
# nvidia-*/triton never enter requirements.txt at all any more, so the old
# grep-strip-and-reinstall-torch-separately dance is gone. --extra-index-url
# is still needed here because that exact "+cpu" version string only
# exists on download.pytorch.org, not on PyPI -- a plain `pip install`
# without it would fail to locate this pin (confirmed: reproduced that
# failure before adding the flag). --no-deps is safe because
# requirements.txt is uv's fully-resolved, hash-pinned closure already.
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-deps -r requirements.txt \
        --extra-index-url https://download.pytorch.org/whl/cpu \
    && /opt/venv/bin/pip check || true

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH"
RUN useradd --create-home --uid 1000 appuser
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY --chown=appuser:appuser app ./app
# evals/ is needed here too -- the ci_gate Cloud Run Job (infra/jobs.tf)
# runs `python3 -m evals.runners.ci_gate` from this same image. One venv
# now covers both app and eval deps (ragas + langgraph coexist -- see
# evals/runners/_ragas_compat.py; this supersedes the old .venv-eval split
# from DECISIONS.md D37-D39).
COPY --chown=appuser:appuser evals ./evals
USER appuser
EXPOSE 8080
CMD exec uvicorn app.api.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1
