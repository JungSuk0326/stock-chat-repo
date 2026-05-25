# syntax=docker/dockerfile:1.7

# ---------------------------------------------------------------------------
# Builder stage: install deps into a venv, then copy app on top
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

# Install uv (the package manager) — small, fast layer
RUN pip install --no-cache-dir uv

# Resolve dependencies first (cached layer until pyproject/uv.lock changes)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Copy app source and install the project
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
RUN uv sync --frozen --no-dev


# ---------------------------------------------------------------------------
# Runtime stage: minimal image with the prebuilt .venv
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=builder /app /app

# Drop root before running anything
RUN useradd --create-home --shell /bin/bash app && \
    chown -R app:app /app
USER app

EXPOSE 8000

# Default command for the `backend` service. The `worker` service overrides
# this via docker-compose.yml `command:` directive.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
