"""Chat session + message persistence service.

A "session" is one threaded conversation about one instrument; multiple
sessions per (user, instrument) are allowed. Messages within a session
are stored in chronological order; the LLM call's accounting (model,
in/out tokens) is captured on the assistant rows so we can audit later.

Auto-title generation runs as a background task after the first
exchange — see schedule_auto_title() in this module.
"""

from __future__ import annotations

import asyncio

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal
from app.llm.base import ChatMessage as LLMChatMessage
from app.llm.registry import LLMRegistry
from app.models import ChatMessage, ChatSession, Instrument

log = structlog.get_logger()

AUTO_TITLE_MAX_LEN = 40
AUTO_TITLE_PROVIDER = "gemini"
AUTO_TITLE_MODEL = "gemini-2.5-flash"
AUTO_TITLE_SYSTEM = (
    "당신은 채팅 세션 제목 생성기다. 사용자의 첫 질문과 한국 주식 종목 정보를 "
    "보고, 그 대화의 주제를 한 줄(최대 30자)로 압축해라. 따옴표, 마침표, 줄바꿈은 "
    "쓰지 마라. 핵심 키워드 위주로 간결하게."
)


async def get_session(db: AsyncSession, session_id: int, user_id: int) -> ChatSession:
    """Fetch one session, scoped by user_id. Raises NoResultFound when
    the session does not exist OR belongs to another user. We do not
    leak existence-vs-ownership in the error — caller maps to 404."""
    stmt = (
        select(ChatSession)
        .where(ChatSession.id == session_id)
        .where(ChatSession.user_id == user_id)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise NoResultFound(f"chat session {session_id} not found")
    return row


async def list_sessions(
    db: AsyncSession,
    *,
    user_id: int,
    instrument_id: int | None = None,
    limit: int = 50,
) -> list[tuple[ChatSession, int]]:
    """Sessions for `user_id`, optionally filtered by instrument, newest
    first. Returns (session, message_count) so the UI can show "12 messages"
    next to a session title without a follow-up query."""
    msg_count = (
        select(
            ChatMessage.session_id,
            func.count(ChatMessage.id).label("n"),
        )
        .group_by(ChatMessage.session_id)
        .subquery()
    )

    stmt = (
        select(ChatSession, func.coalesce(msg_count.c.n, 0))
        .outerjoin(msg_count, msg_count.c.session_id == ChatSession.id)
        .where(ChatSession.user_id == user_id)
        .order_by(ChatSession.updated_at.desc(), ChatSession.id.desc())
        .limit(limit)
    )
    if instrument_id is not None:
        stmt = stmt.where(ChatSession.instrument_id == instrument_id)

    rows = (await db.execute(stmt)).all()
    return [(s, int(c)) for s, c in rows]


async def create_session(
    db: AsyncSession,
    *,
    user_id: int,
    instrument_id: int,
    title: str | None = None,
) -> ChatSession:
    session = ChatSession(
        user_id=user_id, instrument_id=instrument_id, title=title
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def list_messages(
    db: AsyncSession, *, session_id: int
) -> list[ChatMessage]:
    """Chronological replay of a session."""
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def append_message(
    db: AsyncSession,
    *,
    session_id: int,
    role: str,
    content: str,
    model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> ChatMessage:
    """Append one message and bump the session's updated_at so list order
    stays "most-recently-touched on top". Committed by the caller's
    transaction unit (we call commit here for now — keeps callers simple)."""
    msg = ChatMessage(
        session_id=session_id,
        role=role,
        content=content,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    db.add(msg)
    await db.execute(
        update(ChatSession)
        .where(ChatSession.id == session_id)
        .values(updated_at=func.now())
    )
    await db.commit()
    await db.refresh(msg)
    return msg


async def delete_session(
    db: AsyncSession, *, session_id: int, user_id: int
) -> None:
    """Delete a session (messages cascade via FK)."""
    session = await get_session(db, session_id, user_id)
    await db.delete(session)
    await db.commit()


# ---------- Auto-title ----------


def schedule_auto_title(
    registry: LLMRegistry,
    session_id: int,
    user_question: str,
    instrument_name: str | None,
    canonical_id: str,
) -> None:
    """Fire-and-forget background task that asks a cheap LLM to title the
    session. Failures are swallowed — a missing title is fine, the UI
    falls back to "YYYY-MM-DD HH:mm" rendering."""
    asyncio.create_task(
        _generate_auto_title(
            registry, session_id, user_question, instrument_name, canonical_id
        ),
        name=f"chat_auto_title_{session_id}",
    )


async def _generate_auto_title(
    registry: LLMRegistry,
    session_id: int,
    user_question: str,
    instrument_name: str | None,
    canonical_id: str,
) -> None:
    client = registry.get(AUTO_TITLE_PROVIDER)
    if client is None or not client.configured:
        log.info("chat.auto_title.no_provider", session_id=session_id)
        return

    label = instrument_name or canonical_id
    prompt = f"종목: {label} ({canonical_id})\n첫 질문: {user_question}"

    try:
        result = await client.ask(
            system=AUTO_TITLE_SYSTEM,
            messages=[LLMChatMessage(role="user", content=prompt)],
            model=AUTO_TITLE_MODEL,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "chat.auto_title.llm_failed",
            session_id=session_id,
            error=str(exc),
        )
        return

    title = (result.text or "").strip().strip('"').strip("'").splitlines()[0]
    if not title:
        return
    if len(title) > AUTO_TITLE_MAX_LEN:
        title = title[:AUTO_TITLE_MAX_LEN].rstrip()

    async with SessionLocal() as db:
        await db.execute(
            update(ChatSession)
            .where(ChatSession.id == session_id)
            .values(title=title)
        )
        await db.commit()
    log.info("chat.auto_title.set", session_id=session_id, title=title)
