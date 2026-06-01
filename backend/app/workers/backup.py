"""Daily PostgreSQL backup (R5).

Runs pg_dump to a gzipped file in `settings.BACKUP_DIR`. Retention is
managed in-process — files older than `BACKUP_RETENTION_DAYS` are removed
after each successful dump. Off-site replication (e.g. Synology Hyper
Backup → B2/S3) is out of scope — operator sets that up at the host
level pointing at the same dir.

Failure modes covered:
  - pg_dump binary missing  → log warn, mark heartbeat failure, no crash
  - pg_dump returns nonzero → partial file removed, error captured
  - BACKUP_DIR unwritable   → log error, mark heartbeat failure
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import structlog

from app.core.config import get_settings

log = structlog.get_logger()


def _strip_asyncpg(dsn: str) -> str:
    """`postgresql+asyncpg://...` → `postgresql://...`. pg_dump doesn't
    understand SQLAlchemy driver suffixes."""
    return re.sub(r"^postgresql\+\w+://", "postgresql://", dsn)


def _filename(ts: datetime) -> str:
    return f"dump-{ts.strftime('%Y%m%d-%H%M')}.sql.gz"


async def _prune_old(backup_dir: Path, retention_days: int) -> int:
    """Delete dump files older than retention_days. Returns count pruned."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    pruned = 0
    for path in backup_dir.glob("dump-*.sql.gz"):
        try:
            # Use mtime — derived from the filename would also work but
            # mtime is simpler and matches actual storage age.
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                path.unlink(missing_ok=True)
                pruned += 1
        except OSError as exc:
            log.warning("backup.prune_failed", path=str(path), error=str(exc))
    return pruned


async def run_backup() -> None:
    """One-shot pg_dump invocation. Called by the worker on a daily cron.

    The function is heartbeat-wrapped at scheduler.add_job time, so any
    exception is captured into /health's `backup_daily` entry.
    """
    settings = get_settings()
    backup_dir = Path(settings.BACKUP_DIR)
    backup_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc)
    out_path = backup_dir / _filename(ts)
    tmp_path = out_path.with_suffix(out_path.suffix + ".partial")

    dsn = _strip_asyncpg(settings.DATABASE_URL)
    # Hide the password in logs — parse it back out for the redaction.
    parsed = urlparse(dsn)
    db_label = f"{parsed.hostname}:{parsed.port or 5432}/{parsed.path.lstrip('/')}"

    log.info("backup.started", target=str(out_path), db=db_label)

    # pg_dump <dsn> | gzip -c > out
    # Use bash with `set -o pipefail` so a pg_dump failure propagates as
    # nonzero — plain sh pipes would mask it under gzip's 0 exit code, and
    # we'd happily create empty .sql.gz files.
    cmd = (
        "set -o pipefail; "
        f"pg_dump --no-owner --no-privileges {_shell_quote(dsn)} "
        f"| gzip -c > {_shell_quote(str(tmp_path))}"
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            "/bin/bash",
            "-c",
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
    except FileNotFoundError as exc:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(f"bash missing: {exc}") from exc

    if proc.returncode != 0:
        tmp_path.unlink(missing_ok=True)
        err = (stderr or b"").decode(errors="replace").strip()[:500]
        if "pg_dump" in err and "not found" in err:
            raise RuntimeError(
                "pg_dump binary not found — install postgresql-client "
                "in the worker image, or set BACKUP_DIR to disable on host"
            )
        if "server version" in err and "version mismatch" in err:
            raise RuntimeError(
                "pg_dump version too old for the server. Worker image "
                "must include postgresql-client matching the server major "
                f"version. stderr: {err}"
            )
        raise RuntimeError(f"pg_dump failed (rc={proc.returncode}): {err}")

    # Atomic rename so concurrent readers never see a half-written file.
    tmp_path.rename(out_path)
    size_mb = out_path.stat().st_size / (1024 * 1024)
    pruned = await _prune_old(backup_dir, settings.BACKUP_RETENTION_DAYS)
    log.info(
        "backup.done",
        target=str(out_path),
        size_mb=round(size_mb, 2),
        pruned=pruned,
    )


def _shell_quote(s: str) -> str:
    """Single-quote a shell argument. We're not invoking with user input,
    but the DSN can contain `:` and `@` which look weird unescaped."""
    return "'" + s.replace("'", "'\\''") + "'"
