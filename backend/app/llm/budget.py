"""LLM token budget — hard cap for Anthropic spend (R2).

Why this module exists:
  Anthropic charges per-token. An infinite loop or runaway worker could burn
  hundreds of dollars before anyone notices. This module enforces a hard
  daily + monthly cap counted in Redis (atomic INCRBY so racy callers can't
  bypass it).

Usage:
  budget = LLMBudget(redis_client, daily_limit=100_000, monthly_limit=2_000_000)
  await budget.check()             # raises LLMBudgetExceeded if over
  ... call Anthropic ...
  await budget.record(in_tok, out_tok)

Input vs output tokens cost differently (opus-4-7: input $15/MTok, output
$75/MTok = 5x). For simplicity we sum them flat — the user sets a single
combined cap with this multiplier in mind. Switch to cost-weighted
accumulation if precision matters later.
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog
from redis.asyncio import Redis

log = structlog.get_logger()


class LLMBudgetExceeded(Exception):
    """Raised when daily or monthly cap is reached. Caller should 429 the user."""


class LLMBudget:
    def __init__(
        self,
        redis: Redis,
        daily_limit: int,
        monthly_limit: int,
    ) -> None:
        self.redis = redis
        self.daily_limit = daily_limit
        self.monthly_limit = monthly_limit

    @staticmethod
    def _daily_key(now: datetime) -> str:
        return f"llm:tokens:daily:{now.strftime('%Y-%m-%d')}"

    @staticmethod
    def _monthly_key(now: datetime) -> str:
        return f"llm:tokens:monthly:{now.strftime('%Y-%m')}"

    async def usage(self) -> dict[str, int]:
        """Return current daily/monthly token usage (read-only)."""
        now = datetime.now(timezone.utc)
        daily_raw = await self.redis.get(self._daily_key(now))
        monthly_raw = await self.redis.get(self._monthly_key(now))
        return {
            "daily": int(daily_raw) if daily_raw else 0,
            "daily_limit": self.daily_limit,
            "monthly": int(monthly_raw) if monthly_raw else 0,
            "monthly_limit": self.monthly_limit,
        }

    async def check(self) -> None:
        """Raise LLMBudgetExceeded if either cap is already met or exceeded.

        Called BEFORE issuing the Anthropic request, so a single in-flight
        request can still go through if it pushes us slightly over (the next
        call will block). Tight enough for hobby use; switch to reserve-then-
        commit if precision becomes critical.
        """
        u = await self.usage()
        if u["daily"] >= u["daily_limit"]:
            raise LLMBudgetExceeded(
                f"daily cap reached: {u['daily']}/{u['daily_limit']} tokens"
            )
        if u["monthly"] >= u["monthly_limit"]:
            raise LLMBudgetExceeded(
                f"monthly cap reached: {u['monthly']}/{u['monthly_limit']} tokens"
            )

    async def record(self, input_tokens: int, output_tokens: int) -> None:
        """Atomically add this call's usage to the daily and monthly counters."""
        now = datetime.now(timezone.utc)
        total = max(input_tokens, 0) + max(output_tokens, 0)
        daily_key = self._daily_key(now)
        monthly_key = self._monthly_key(now)

        pipe = self.redis.pipeline()
        pipe.incrby(daily_key, total)
        pipe.expire(daily_key, 2 * 86400)  # 2 days, slightly past UTC midnight
        pipe.incrby(monthly_key, total)
        pipe.expire(monthly_key, 35 * 86400)  # 35 days, slightly past month end
        results = await pipe.execute()

        log.info(
            "llm.budget.recorded",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total=total,
            daily=results[0],
            monthly=results[2],
        )
