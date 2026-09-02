# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.12.8 AS uv

FROM python:3.13.15-slim AS builder
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app
COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

FROM python:3.13.15-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends libseccomp2 \
    && rm -rf /var/lib/apt/lists/*
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
RUN groupadd --system --gid 10001 smm \
    && useradd --system --uid 10001 --gid smm --home-dir /app --shell /usr/sbin/nologin smm \
    && mkdir -p /app/.data/media \
    && chown -R smm:smm /app
COPY --from=builder --chown=smm:smm /app/.venv /app/.venv
COPY --chown=smm:smm alembic.ini ./
COPY --chown=smm:smm migrations ./migrations
USER smm
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)"]
CMD ["uvicorn", "smm_gpt.application:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
