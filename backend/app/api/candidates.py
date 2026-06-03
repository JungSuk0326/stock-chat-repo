"""Candidate REST — list + lifecycle actions.

Actions:
  - POST /candidates/{id}/promote  → adds to watchlist + status=promoted
  - POST /candidates/{id}/snooze   → status=snoozed + snoozed_until=now+days
  - POST /candidates/{id}/dismiss  → status=dismissed (never resurface)

Read endpoint supports filtering by status and source so the UI can do
"new only" or "from screener:3 only" views without client-side filtering.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_id
from app.core.db import get_db
from app.schemas.discovery import (
    CandidateListResponse,
    CandidateSnoozeRequest,
    CandidateSummary,
)
from app.services.discovery_crud import (
    dismiss_candidate,
    get_instrument,
    list_candidates,
    promote_candidate,
    snooze_candidate,
)

router = APIRouter(prefix="/candidates", tags=["candidates"])


async def _to_summary(db: AsyncSession, candidate) -> CandidateSummary:
    inst = await get_instrument(db, candidate.instrument_id)
    canonical = f"{inst.exchange}:{inst.symbol}" if inst else "?"
    inst_name = inst.name if inst else None
    return CandidateSummary(
        id=candidate.id,
        instrument=canonical,
        instrument_name=inst_name,
        source=candidate.source,
        score=candidate.score,
        reason=candidate.reason,
        status=candidate.status,
        discovered_at=candidate.discovered_at,
        snoozed_until=candidate.snoozed_until,
        updated_at=candidate.updated_at,
    )


@router.get("", response_model=CandidateListResponse)
async def list_user_candidates(
    status: str | None = Query(default=None, max_length=16),
    source: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> CandidateListResponse:
    """List candidates. `status` may be one of:
      "new" | "snoozed" | "promoted" | "dismissed" | "active"

    "active" is a convenience meaning "needs review" — new OR snoozed-but-due.
    Omit for "everything".
    """
    rows = await list_candidates(
        db, user_id=user_id, status=status, source=source, limit=limit
    )
    items = [await _to_summary(db, r) for r in rows]
    return CandidateListResponse(count=len(items), items=items)


@router.post("/{candidate_id}/promote", response_model=CandidateSummary)
async def promote(
    candidate_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> CandidateSummary:
    try:
        row = await promote_candidate(
            db, candidate_id=candidate_id, user_id=user_id
        )
    except NoResultFound:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return await _to_summary(db, row)


@router.post("/{candidate_id}/snooze", response_model=CandidateSummary)
async def snooze(
    candidate_id: int,
    body: CandidateSnoozeRequest,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> CandidateSummary:
    try:
        row = await snooze_candidate(
            db, candidate_id=candidate_id, user_id=user_id, days=body.days
        )
    except NoResultFound:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return await _to_summary(db, row)


@router.post("/{candidate_id}/dismiss", response_model=CandidateSummary)
async def dismiss(
    candidate_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> CandidateSummary:
    try:
        row = await dismiss_candidate(
            db, candidate_id=candidate_id, user_id=user_id
        )
    except NoResultFound:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return await _to_summary(db, row)
