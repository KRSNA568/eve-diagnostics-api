"""Model registry.

Importing this module registers every ORM model on `Base.metadata`, which Alembic
autogenerate and the test suite rely on. Add each new feature's models here.
"""

from eve.auth.models import User
from eve.catalog.models import CentreTest, DiagnosticCentre, DiagnosticTest
from eve.core.db import Base

__all__ = ["Base", "CentreTest", "DiagnosticCentre", "DiagnosticTest", "User"]
