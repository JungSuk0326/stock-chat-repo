"""Market-wide investor-type net trading per (date, market, investor).

Long-format: one row per (trade_date, market, investor_type) so the
schema absorbs new investor types from KRX without migration.

KRX publishes 11 investor types in the detailed breakdown:

    financial_investment  금융투자
    insurance             보험
    investment_trust      투신
    private_fund          사모
    bank                  은행
    other_finance         기타금융
    pension               연기금
    other_corp            기타법인
    individual            개인
    foreign               외국인
    other_foreign         기타외국인

We also store the canonical *aggregate* `institutional` (= sum of the
first 8 in KRX terms — 금융투자+보험+투신+사모+은행+기타금융+연기금+기타법인)
when the source provides it directly, so consumers can query "기관 전체
누적" without summing 8 rows.

`net_value` is signed KRW (positive = net buy, negative = net sell).
`market` mirrors KRX's `mktId` codes: `STK` (KOSPI), `KSQ` (KOSDAQ),
`ALL` (전체 시장 합계).
"""

from datetime import date as date_type, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MarketInvestorFlow(Base):
    __tablename__ = "market_investor_flows"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    trade_date: Mapped[date_type] = mapped_column(Date, nullable=False)
    # KRX mktId: STK / KSQ / ALL
    market: Mapped[str] = mapped_column(String(8), nullable=False)
    # canonical investor type — see module docstring for the closed set
    investor_type: Mapped[str] = mapped_column(String(32), nullable=False)

    # signed KRW. + = net buy by this investor type, - = net sell.
    net_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # optional: total buy/sell legs (some sources provide both; nullable
    # so the table remains usable from sources that only emit net)
    buy_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sell_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    source: Mapped[str] = mapped_column(String(16), nullable=False, default="krx")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "trade_date", "market", "investor_type",
            name="uq_market_investor_flows_date_market_investor",
        ),
        # "특정 투자자가 최근 N일 시장 전체에서 얼마나 샀나" — 자연어 발굴의
        # 가장 흔한 쿼리 패턴. (investor_type, trade_date) 순으로 인덱싱.
        Index(
            "ix_market_investor_flows_investor_date",
            "investor_type", "trade_date",
        ),
    )


# Canonical investor_type values. Keep this list in sync with the
# KrMarketInvestorFlowAdapter mapping; everything that talks to the DB
# (worker, REST, LLM tool) should import from here, never hardcode the
# string.
INVESTOR_TYPES: tuple[str, ...] = (
    "financial_investment",  # 금융투자
    "insurance",              # 보험
    "investment_trust",       # 투신
    "private_fund",           # 사모
    "bank",                   # 은행
    "other_finance",          # 기타금융
    "pension",                # 연기금
    "other_corp",             # 기타법인
    "individual",             # 개인
    "foreign",                # 외국인
    "other_foreign",          # 기타외국인
    "institutional",          # 기관 합계 (선택적)
)

# Korean label map — used by REST/LLM responses for UI display.
INVESTOR_TYPE_LABELS_KO: dict[str, str] = {
    "financial_investment": "금융투자",
    "insurance": "보험",
    "investment_trust": "투신",
    "private_fund": "사모",
    "bank": "은행",
    "other_finance": "기타금융",
    "pension": "연기금",
    "other_corp": "기타법인",
    "individual": "개인",
    "foreign": "외국인",
    "other_foreign": "기타외국인",
    "institutional": "기관",
}

# Markets KRX exposes via the detailed-view endpoint.
MARKETS: tuple[str, ...] = ("STK", "KSQ", "ALL")
