"""ORM models package.

Importing this package registers all models with Base.metadata,
which is what Alembic's autogenerate inspects.
"""

from app.models.base import Base
from app.models.disclosure import CorpCode, Disclosure
from app.models.instrument import Instrument
from app.models.price import Price
from app.models.watchlist import WatchlistEntry

__all__ = [
    "Base",
    "CorpCode",
    "Disclosure",
    "Instrument",
    "Price",
    "WatchlistEntry",
]
