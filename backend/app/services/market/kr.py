import asyncio
from collections.abc import Sequence

import FinanceDataReader as fdr
import structlog

from app.services.market.base import InstrumentData, MarketAdapter

log = structlog.get_logger()


class KrMarketAdapter(MarketAdapter):
    """Korean market (KOSPI + KOSDAQ) adapter.

    Primary data source: FinanceDataReader (one bulk call → entire KRX listing).
    pykrx will be added later for prices and fundamentals.
    """

    market_code = "KR"

    async def fetch_instruments(self) -> Sequence[InstrumentData]:
        # fdr.StockListing is a synchronous HTTP call. Wrap with to_thread.
        df = await asyncio.to_thread(fdr.StockListing, "KRX")
        log.info("kr.fetch_instruments.raw", rows=len(df))

        # Column naming has shifted across FDR versions; handle both.
        symbol_col = "Code" if "Code" in df.columns else "Symbol"
        name_col = "Name" if "Name" in df.columns else None
        market_col = "Market" if "Market" in df.columns else None

        out: list[InstrumentData] = []
        for _, row in df.iterrows():
            symbol_raw = str(row[symbol_col]).strip() if symbol_col in df.columns else ""
            if not symbol_raw.isdigit():
                # Skip ETFs / preferred shares / REITs that don't fit 6-digit codes
                continue

            market = (
                str(row[market_col]).strip()
                if market_col and market_col in df.columns
                else None
            )
            # Phase 1: regular equity on KOSPI/KOSDAQ only. Skip KONEX, etc.
            if market not in ("KOSPI", "KOSDAQ"):
                continue

            name = (
                str(row[name_col]).strip()
                if name_col and name_col in df.columns
                else None
            )

            out.append(
                InstrumentData(
                    exchange="KR",
                    symbol=symbol_raw.zfill(6),  # ensure 6-digit zero-padded
                    country="KR",
                    currency="KRW",
                    market=market,
                    name=name,
                    isin=None,  # FDR does not provide ISIN
                )
            )

        log.info("kr.fetch_instruments.parsed", count=len(out))
        return out
