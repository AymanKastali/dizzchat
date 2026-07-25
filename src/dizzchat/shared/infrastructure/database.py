"""Shared SQLAlchemy declarative base.

Every bounded context maps its persistence models onto this ``Base`` so a single
``Base.metadata`` drives Alembic autogeneration. Concrete models arrive in later slices.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base shared across all bounded contexts."""
