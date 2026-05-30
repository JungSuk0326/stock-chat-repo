"""Disclosure adapter abstraction.

Phase 1 implements `DartAdapter` for the Korean market (DART OpenAPI).
Phase 2 will add a US adapter backed by SEC EDGAR. Higher-level callers
(worker, assemble_context) depend on this interface only.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import date, datetime

from pydantic import BaseModel, Field


class CorpCodeEntry(BaseModel):
    """One row of the regulator's corp-id ↔ ticker mapping.

    DART: corp_code (8-digit), stock_code (6-digit), corp_name.
    SEC (Phase 2): CIK (10-digit), ticker, name.
    """

    source: str = Field(..., max_length=16)  # "dart" | "sec"
    source_corp_id: str = Field(..., max_length=32)  # DART corp_code, SEC CIK, ...
    exchange: str = Field(..., max_length=8)  # KR, US, ...
    symbol: str = Field(..., max_length=32)
    name: str | None = Field(default=None, max_length=255)


class DisclosureData(BaseModel):
    """One disclosure / filing.

    `filed_at` is the official publication timestamp in UTC. `source_id` is
    the regulator's stable identifier for the filing (DART receipt_no,
    SEC accession number) — used for idempotent UPSERT.
    """

    source: str = Field(..., max_length=16)  # "dart" | "sec"
    source_id: str = Field(..., max_length=32)
    exchange: str = Field(..., max_length=8)
    symbol: str = Field(..., max_length=32)
    title: str = Field(..., max_length=512)
    filed_at: datetime
    report_type: str | None = Field(default=None, max_length=64)
    submitter: str | None = Field(default=None, max_length=255)
    raw_url: str | None = Field(default=None, max_length=512)


class DisclosureAdapter(ABC):
    """Per-market regulatory disclosure source.

    Each market (KR=DART, US=SEC EDGAR, ...) implements this.
    """

    #: Internal source identifier — matches DisclosureData.source / CorpCodeEntry.source
    source: str

    @abstractmethod
    async def fetch_corp_codes(self) -> Sequence[CorpCodeEntry]:
        """Return the full regulator-side corp-id ↔ ticker mapping.

        For DART this is the ZIP-packed CORPCODE.xml file (refreshed
        daily). For SEC this is the company_tickers.json file. Either way,
        callers UPSERT the result into `corp_codes`.
        """
        ...

    @abstractmethod
    async def fetch_recent_disclosures(
        self,
        source_corp_id: str,
        start: date,
        end: date,
    ) -> Sequence[DisclosureData]:
        """Return disclosures filed in [start, end] (inclusive) for the given
        regulator corp id. Empty Sequence if none.

        Caller is responsible for converting (exchange, symbol) → source_corp_id
        via the `corp_codes` table before invoking.
        """
        ...
