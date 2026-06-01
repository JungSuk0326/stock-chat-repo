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

# postgresql-client-16 provides pg_dump for the daily backup job (R5).
# Must match the server major version (we run TimescaleDB on pg16) — Debian
# bookworm ships pg15 by default, so we pull from the official postgres apt
# repo. Kept in the runtime image so both backend and worker share one tag.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg gzip \
    && install -d /usr/share/keyrings \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        | gpg --dearmor -o /usr/share/keyrings/postgresql.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/postgresql.gpg] http://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-16 \
    && apt-get purge -y curl gnupg \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app /app

# Drop root before running anything
RUN useradd --create-home --shell /bin/bash app && \
    chown -R app:app /app
USER app

EXPOSE 8000

# Default command for the `backend` service. The `worker` service overrides
# this via docker-compose.yml `command:` directive.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
