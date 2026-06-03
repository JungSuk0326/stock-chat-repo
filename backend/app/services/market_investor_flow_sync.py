"""Persist market-wide investor-flow rows via the adapter.

Idempotent: ON CONFLICT (trade_date, market, investor_type) DO NOTHING.
The adapter typically emits 11 rows × N days × M markets so re-running
the daily sweep with overlap is cheap.
"""

from __future__ import annotations

from datetime import date, timedelta

import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.db import SessionLocal
from app.models.market_investor_flow import MarketInvestorFlow
from app.services.market_investor_flow.base import MarketInvestorFlowAdapter

log = structlog.get_logger()

# Markets to sweep daily. Both individual exchanges; "ALL" is the
# market-wide aggregate, which we also cache because LLM queries often
# ask "근 한 달간 사모 순매수" without specifying KOSPI vs KOSDAQ.
DEFAULT_MARKETS: tuple[str, ...] = ("STK", "KSQ", "ALL")


async def sync_market_investor_flows(
    adapter: MarketInvestorFlowAdapter,
    *,
    start: date,
    end: date,
    markets: tuple[str, ...] = DEFAULT_MARKETS,
) -> int:
    """Fetch [start, end] for each market and UPSERT.

    Returns the count of *new* rows inserted (existing rows hit the
    ON CONFLICT no-op path and don't contribute)."""
    total_new = 0
    for market in markets:
        rows = await adapter.fetch_daily(start, end, market=market)
        if not rows:
            log.info(
                "market_investor_flow.empty",
                market=market,
                start=str(start),
                end=str(end),
            )
            continue

        values = [
            {
                "trade_date": r.trade_date,
                "market": r.market,
                "investor_type": r.investor_type,
                "net_value": r.net_value,
                "buy_value": r.buy_value,
                "sell_value": r.sell_value,
                "source": r.source,
            }
            for r in rows
        ]

        async with SessionLocal() as session:
            stmt = pg_insert(MarketInvestorFlow).values(values)
            stmt = stmt.on_conflict_do_nothing(
                constraint="uq_market_investor_flows_date_market_investor",
            )
            # `rowcount` reflects only new inserts because postgres
            # reports affected rows excluding skipped conflicts.
            result = await session.execute(stmt)
            await session.commit()
            new = result.rowcount or 0
            total_new += new
            log.info(
                "market_investor_flow.synced",
                market=market,
                start=str(start),
                end=str(end),
                rows=len(rows),
                new=new,
            )

    return total_new


async def daily_market_investor_flow_tick(
    adapter: MarketInvestorFlowAdapter,
    *,
    lookback_days: int = 7,
) -> int:
    """Daily worker entry point. Re-fetches the last N trading days so a
    short worker outage still catches up — UNIQUE handles dedup."""
    end = date.today()
    start = end - timedelta(days=lookback_days)
    return await sync_market_investor_flows(adapter, start=start, end=end)
