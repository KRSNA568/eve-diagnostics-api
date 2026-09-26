from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from eve.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin

# Relationships use lazy="raise": in async code an implicit lazy load is a bug (and an N+1
# query in disguise), so every load must be explicit in the query.


class DiagnosticCentre(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "diagnostic_centres"
    __table_args__ = (
        CheckConstraint("pincode ~ '^[1-9][0-9]{5}$'", name="pincode_format"),
        # Supports the case-insensitive `?city=` filter.
        Index("ix_diagnostic_centres_city_lower", text("lower(city)")),
    )

    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(String(500))
    city: Mapped[str] = mapped_column(String(100))
    pincode: Mapped[str] = mapped_column(String(6))
    # Soft delete: deactivated centres disappear from the catalog but past bookings keep
    # a valid reference.
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())

    offerings: Mapped[list["CentreTest"]] = relationship(back_populates="centre", lazy="raise")


class DiagnosticTest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "diagnostic_tests"
    __table_args__ = (CheckConstraint("code = upper(code)", name="code_uppercase"),)

    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())


class CentreTest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A test offered by a centre, at that centre's price."""

    __tablename__ = "centre_tests"
    __table_args__ = (
        UniqueConstraint("centre_id", "test_id"),
        CheckConstraint("price > 0", name="price_positive"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso4217"),
    )

    centre_id: Mapped[UUID] = mapped_column(
        ForeignKey("diagnostic_centres.id", ondelete="RESTRICT")
    )
    # Indexed for "which centres offer test X"; (centre_id, test_id) is covered by the
    # unique constraint's index.
    test_id: Mapped[UUID] = mapped_column(
        ForeignKey("diagnostic_tests.id", ondelete="RESTRICT"), index=True
    )
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3), default="INR", server_default="INR")
    is_available: Mapped[bool] = mapped_column(default=True, server_default=true())

    centre: Mapped[DiagnosticCentre] = relationship(back_populates="offerings", lazy="raise")
    test: Mapped[DiagnosticTest] = relationship(lazy="raise")
