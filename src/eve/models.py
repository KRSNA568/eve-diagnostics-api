"""Model registry.

Importing this module registers every ORM model on `Base.metadata`, which Alembic
autogenerate and the test suite rely on. Add each new feature's models here.
"""

from eve.core.db import Base

__all__ = ["Base"]
