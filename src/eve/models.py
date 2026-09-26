"""Model registry.

Importing this module registers every ORM model on `Base.metadata`, which Alembic
autogenerate and the test suite rely on. Add each new feature's models here.
"""

from eve.auth.models import User
from eve.bookings.models import Booking
from eve.catalog.models import CentreTest, DiagnosticCentre, DiagnosticTest
from eve.core.db import Base
from eve.payments.models import Payment, WebhookEvent

__all__ = [
    "Base",
    "Booking",
    "CentreTest",
    "DiagnosticCentre",
    "DiagnosticTest",
    "Payment",
    "User",
    "WebhookEvent",
]
