from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Common declarative base for all ORM models.

    All models must inherit from this class so they share a single MetaData
    instance. Alembic uses Base.metadata for autogenerate.
    """
