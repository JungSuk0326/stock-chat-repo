"""ORM models package.

Importing this package registers all models with Base.metadata,
which is what Alembic's autogenerate inspects.
"""

from app.models.base import Base
from app.models.instrument import Instrument

__all__ = ["Base", "Instrument"]
