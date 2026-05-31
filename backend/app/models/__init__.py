"""ORM models package.

Importing this package registers all models with Base.metadata,
which is what Alembic's autogenerate inspects.
"""

from app.models.alert import AlertEvent, AlertRule
from app.models.base import Base
from app.models.chat import ChatMessage, ChatSession
from app.models.disclosure import CorpCode, Disclosure
from app.models.instrument import Instrument
from app.models.price import Price
from app.models.user import OWNER_USER_ID, User
from app.models.watchlist import WatchlistEntry

__all__ = [
    "AlertEvent",
    "AlertRule",
    "Base",
    "ChatMessage",
    "ChatSession",
    "CorpCode",
    "Disclosure",
    "Instrument",
    "OWNER_USER_ID",
    "Price",
    "User",
    "WatchlistEntry",
]
