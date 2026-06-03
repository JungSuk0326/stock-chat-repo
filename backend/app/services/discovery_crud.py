"""Screener + Candidate CRUD service.

Sits alongside `discovery.py` (which holds the evaluation engine). Kept
separate so the engine module stays focused on the math/SQL and this
module stays focused on routing-facing operations.

Promote action interacts with `watchlist`: insert a row if the symbol
isn't already watched, then mark the candidate as promoted. We don't
fire the price/disclosure/news backfill inline — the worker's
reconcile_watchlist (30s interval) picks up the new entry and runs
them shortly after. The watchlist HTTP endpoint does inline backfill;
discovery doesn't, intentionally, since the user is browsing candidates
not actively watching the new symbol yet.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Candidate, Instrument, Screener, WatchlistEntry

log = structlog.get_logger()


# ----- Screener CRUD -----


async def list_screeners(
    db: AsyncSession, *, user_id: int
) -> list[Screener]:
    stmt = (
        select(Screener)
        .where(Screener.user_id == user_id)
        .order_by(Screener.updated_at.desc(), Screener.id.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def get_screener(
    db: AsyncSession, *, screener_id: int, user_id: int
) -> Screener:
    stmt = (
        select(Screener)
        .where(Screener.id == screener_id)
        .where(Screener.user_id == user_id)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise NoResultFound(f"screener {screener_id} not found")
    return row


async def create_screener(
    db: AsyncSession,
    *,
    user_id: int,
    name: str,
    description: str | None,
    universe: dict[str, Any],
    criteria: list[dict[str, Any]],
    enabled: bool = True,
) -> Screener:
    row = Screener(
        user_id=user_id,
        name=name,
        description=description,
        universe=universe or {},
        criteria=criteria or [],
        enabled=enabled,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def update_screener(
    db: AsyncSession,
    *,
    screener_id: int,
    user_id: int,
    name: str | None = None,
    description: str | None = None,
    universe: dict[str, Any] | None = None,
    criteria: list[dict[str, Any]] | None = None,
    enabled: bool | None = None,
) -> Screener:
    row = await get_screener(db, screener_id=screener_id, user_id=user_id)
    if name is not None:
        row.name = name
    if description is not None:
        row.description = description
    if universe is not None:
        row.universe = universe
    if criteria is not None:
        row.criteria = criteria
    if enabled is not None:
        row.enabled = enabled
    await db.commit()
    await db.refresh(row)
    return row


async def delete_screener(
    db: AsyncSession, *, screener_id: int, user_id: int
) -> None:
    row = await get_screener(db, screener_id=screener_id, user_id=user_id)
    await db.delete(row)
    await db.commit()


# ----- Candidate CRUD + actions -----


async def list_candidates(
    db: AsyncSession,
    *,
    user_id: int,
    status: str | None = None,
    source: str | None = None,
    limit: int = 100,
) -> list[Candidate]:
    """List candidates, newest discovery first. status="active" is a
    convenience: returns new + currently-non-active snoozed."""
    stmt = (
        select(Candidate)
        .where(Candidate.user_id == user_id)
    )
    if status == "active":
        # "Active" = needs review = new, OR snoozed-but-due
        now = datetime.now(timezone.utc)
        stmt = stmt.where(
            (Candidate.status == "new")
            | (
                (Candidate.status == "snoozed")
                & (Candidate.snoozed_until <= now)
            )
        )
    elif status is not None:
        stmt = stmt.where(Candidate.status == status)
    if source is not None:
        stmt = stmt.where(Candidate.source == source)
    stmt = stmt.order_by(
        Candidate.discovered_at.desc(), Candidate.id.desc()
    ).limit(limit)
    return list((await db.execute(stmt)).scalars().all())


async def get_candidate(
    db: AsyncSession, *, candidate_id: int, user_id: int
) -> Candidate:
    stmt = (
        select(Candidate)
        .where(Candidate.id == candidate_id)
        .where(Candidate.user_id == user_id)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise NoResultFound(f"candidate {candidate_id} not found")
    return row


async def promote_candidate(
    db: AsyncSession, *, candidate_id: int, user_id: int
) -> Candidate:
    """Add the candidate's instrument to watchlist (if not already there)
    and mark status=promoted. The 30s reconcile loop will start polling
    + backfill; inline backfill is intentionally skipped to keep this
    endpoint snappy — users typically queue up a few promotes before
    switching tabs."""
    candidate = await get_candidate(db, candidate_id=candidate_id, user_id=user_id)

    # Check if already in watchlist (UNIQUE on instrument_id — single-user app)
    existing = (
        await db.execute(
            select(WatchlistEntry).where(
                WatchlistEntry.instrument_id == candidate.instrument_id
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        entry = WatchlistEntry(instrument_id=candidate.instrument_id, position=0)
        db.add(entry)
        try:
            await db.commit()
        except IntegrityError:
            # Race lost: someone else added it. Treat as success.
            await db.rollback()

    candidate.status = "promoted"
    candidate.snoozed_until = None
    await db.commit()
    await db.refresh(candidate)
    log.info(
        "candidate.promoted",
        candidate_id=candidate_id,
        instrument_id=candidate.instrument_id,
    )
    return candidate


async def snooze_candidate(
    db: AsyncSession, *, candidate_id: int, user_id: int, days: int = 7
) -> Candidate:
    candidate = await get_candidate(db, candidate_id=candidate_id, user_id=user_id)
    candidate.status = "snoozed"
    candidate.snoozed_until = datetime.now(timezone.utc) + timedelta(days=days)
    await db.commit()
    await db.refresh(candidate)
    return candidate


async def dismiss_candidate(
    db: AsyncSession, *, candidate_id: int, user_id: int
) -> Candidate:
    """Mark as dismissed — future screener re-runs won't resurface."""
    candidate = await get_candidate(db, candidate_id=candidate_id, user_id=user_id)
    candidate.status = "dismissed"
    candidate.snoozed_until = None
    await db.commit()
    await db.refresh(candidate)
    return candidate


# ----- Helpers used by route layer -----


async def get_instrument(
    db: AsyncSession, instrument_id: int
) -> Instrument | None:
    return await db.get(Instrument, instrument_id)


async def candidate_count_by_screener(
    db: AsyncSession, *, user_id: int, screener_id: int, status: str | None = None
) -> int:
    """Count candidates linked to a screener (matched by source="screener:<id>")."""
    source = f"screener:{screener_id}"
    stmt = select(Candidate).where(
        Candidate.user_id == user_id, Candidate.source == source
    )
    if status is not None:
        stmt = stmt.where(Candidate.status == status)
    rows = (await db.execute(stmt)).scalars().all()
    return len(rows)
