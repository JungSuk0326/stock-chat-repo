"""ORM models package.

Importing this package registers all models with Base.metadata,
which is what Alembic's autogenerate inspects.
"""

from app.models.alert import AlertEvent, AlertRule
from app.models.base import Base
from app.models.chat import ChatMessage, ChatSession
from app.models.disclosure import CorpCode, Disclosure
from app.models.discovery import Candidate, Screener
from app.models.fundamentals import FundamentalsSnapshot
from app.models.instrument import Instrument
from app.models.investor_flow import InvestorFlow
from app.models.market_investor_flow import (
    INVESTOR_TYPE_LABELS_KO,
    INVESTOR_TYPES,
    MARKETS,
    MarketInvestorFlow,
)
from app.models.news import NewsItem
from app.models.price import Price
from app.models.user import OWNER_USER_ID, User
from app.models.watchlist import WatchlistEntry

__all__ = [
    "AlertEvent",
    "AlertRule",
    "Base",
    "Candidate",
    "ChatMessage",
    "ChatSession",
    "CorpCode",
    "Disclosure",
    "FundamentalsSnapshot",
    "Instrument",
    "INVESTOR_TYPE_LABELS_KO",
    "INVESTOR_TYPES",
    "InvestorFlow",
    "MARKETS",
    "MarketInvestorFlow",
    "NewsItem",
    "OWNER_USER_ID",
    "Price",
    "Screener",
    "User",
    "WatchlistEntry",
]
