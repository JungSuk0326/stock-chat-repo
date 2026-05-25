import asyncio
from collections.abc import Sequence
from datetime import date, datetime, timezone
from decimal import Decimal

import FinanceDataReader as fdr
import structlog
from pykrx import stock as pykrx_stock

from app.services.market.base import InstrumentData, MarketAdapter, PriceData

log = structlog.get_logger()


class KrMarketAdapter(MarketAdapter):
    """Korean market (KOSPI + KOSDAQ) adapter.

    - Instrument master: FinanceDataReader (one bulk call → entire KRX listing)
    - EOD prices: pykrx (KRX page backend, one call per (ticker, range))
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

    async def fetch_eod_prices(
        self,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[PriceData]:
        fromdate = start.strftime("%Y%m%d")
        todate = end.strftime("%Y%m%d")
        # pykrx is sync; wrap with to_thread.
        df = await asyncio.to_thread(
            pykrx_stock.get_market_ohlcv_by_date,
            fromdate=fromdate,
            todate=todate,
            ticker=symbol,
        )
        if df is None or df.empty:
            log.warning(
                "kr.fetch_eod_prices.empty",
                symbol=symbol,
                start=fromdate,
                end=todate,
            )
            return []

        out: list[PriceData] = []
        for ts, row in df.iterrows():
            # pykrx returns Korean column names (시가/고가/저가/종가/거래량)
            # and a naive DatetimeIndex at the trading date.
            # Convention: store as midnight UTC of the trading date.
            bar_date = ts.date()
            bar_time = datetime(
                bar_date.year,
                bar_date.month,
                bar_date.day,
                tzinfo=timezone.utc,
            )
            out.append(
                PriceData(
                    time=bar_time,
                    open=Decimal(str(row["시가"])),
                    high=Decimal(str(row["고가"])),
                    low=Decimal(str(row["저가"])),
                    close=Decimal(str(row["종가"])),
                    volume=int(row["거래량"]),
                )
            )

        log.info(
            "kr.fetch_eod_prices.parsed",
            symbol=symbol,
            count=len(out),
            start=fromdate,
            end=todate,
        )
        return out
