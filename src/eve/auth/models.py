from sqlalchemy import CheckConstraint, String, false, true
from sqlalchemy.orm import Mapped, mapped_column

from eve.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        # Emails are stored normalised to lowercase; with the UNIQUE constraint this makes
        # uniqueness case-insensitive at the database level, not just in application code.
        CheckConstraint("email = lower(email)", name="email_lowercase"),
    )

    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())
    is_admin: Mapped[bool] = mapped_column(default=False, server_default=false())
