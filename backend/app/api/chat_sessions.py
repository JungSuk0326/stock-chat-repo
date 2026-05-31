"""Chat session CRUD — ChatGPT-style threaded conversations.

Each session is one threaded chat about one instrument. Multiple sessions
per (user, instrument) are allowed — "I want a fresh new chat about
Samsung even though I already have an old one" is a normal flow.

The actual POST /chat (message append) is still in api/chat.py; this
module is just session management.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_id
from app.core.db import get_db
from app.models import Instrument
from app.schemas.chat_session import (
    ChatMessageRecord,
    ChatSessionCreateRequest,
    ChatSessionDetailResponse,
    ChatSessionListResponse,
    ChatSessionSummary,
)
from app.services.chat_history import (
    create_session,
    delete_session,
    get_session,
    list_messages,
    list_sessions,
)

router = APIRouter(prefix="/chat/sessions", tags=["chat-sessions"])


async def _resolve_instrument(
    db: AsyncSession, exchange: str, symbol: str
) -> Instrument:
    exchange_norm = exchange.upper().strip()
    symbol_norm = symbol.strip()
    inst = (
        await db.execute(
            select(Instrument).where(
                Instrument.exchange == exchange_norm,
                Instrument.symbol == symbol_norm,
            )
        )
    ).scalar_one_or_none()
    if inst is None:
        raise HTTPException(
            status_code=404,
            detail=f"Instrument not found: {exchange_norm}:{symbol_norm}",
        )
    return inst


def _summary(session, instrument: Instrument, message_count: int) -> ChatSessionSummary:
    return ChatSessionSummary(
        id=session.id,
        instrument_id=session.instrument_id,
        instrument=f"{instrument.exchange}:{instrument.symbol}",
        title=session.title,
        message_count=message_count,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


@router.get("", response_model=ChatSessionListResponse)
async def get_sessions(
    exchange: str | None = Query(default=None, max_length=8),
    symbol: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> ChatSessionListResponse:
    """List the caller's sessions, newest-first. When both `exchange` and
    `symbol` are given the list is restricted to that instrument."""
    instrument_id: int | None = None
    if exchange or symbol:
        if not (exchange and symbol):
            raise HTTPException(
                status_code=400,
                detail="exchange and symbol must be provided together",
            )
        inst = await _resolve_instrument(db, exchange, symbol)
        instrument_id = inst.id

    pairs = await list_sessions(
        db, user_id=user_id, instrument_id=instrument_id, limit=limit
    )

    if not pairs:
        return ChatSessionListResponse(count=0, items=[])

    # Bulk-load instruments to render canonical ids without N+1.
    inst_ids = {s.instrument_id for s, _ in pairs}
    insts = {
        i.id: i
        for i in (
            await db.execute(
                select(Instrument).where(Instrument.id.in_(inst_ids))
            )
        ).scalars()
    }

    items = [_summary(s, insts[s.instrument_id], c) for s, c in pairs]
    return ChatSessionListResponse(count=len(items), items=items)


@router.post("", response_model=ChatSessionSummary, status_code=201)
async def post_session(
    body: ChatSessionCreateRequest,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> ChatSessionSummary:
    inst = await _resolve_instrument(db, body.exchange, body.symbol)
    session = await create_session(
        db, user_id=user_id, instrument_id=inst.id, title=body.title
    )
    return _summary(session, inst, message_count=0)


@router.get("/{session_id}", response_model=ChatSessionDetailResponse)
async def get_session_detail(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> ChatSessionDetailResponse:
    try:
        session = await get_session(db, session_id, user_id)
    except NoResultFound:
        raise HTTPException(status_code=404, detail="Session not found")

    inst = (
        await db.execute(
            select(Instrument).where(Instrument.id == session.instrument_id)
        )
    ).scalar_one()

    messages = await list_messages(db, session_id=session.id)
    return ChatSessionDetailResponse(
        session=_summary(session, inst, message_count=len(messages)),
        messages=[ChatMessageRecord.model_validate(m) for m in messages],
    )


@router.delete("/{session_id}", status_code=204)
async def delete_session_endpoint(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> None:
    try:
        await delete_session(db, session_id=session_id, user_id=user_id)
    except NoResultFound:
        raise HTTPException(status_code=404, detail="Session not found")
