"""create bookings

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-26 14:20:50.316511
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bookings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("centre_id", sa.Uuid(), nullable=False),
        sa.Column("test_id", sa.Uuid(), nullable=False),
        sa.Column("appointment_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("amount", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        # Status is VARCHAR + CHECK (not a native enum): adding a state is a plain migration.
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("status_reason", sa.String(length=200), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f("ck_bookings_currency_iso4217")),
        sa.CheckConstraint(
            "status IN ('PENDING', 'CONFIRMED', 'FAILED', 'CANCELLED')",
            name=op.f("ck_bookings_status"),
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_bookings_amount_positive")),
        sa.ForeignKeyConstraint(
            ["centre_id", "test_id"],
            ["centre_tests.centre_id", "centre_tests.test_id"],
            name="fk_bookings_offering",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["centre_id"],
            ["diagnostic_centres.id"],
            name=op.f("fk_bookings_centre_id_diagnostic_centres"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["test_id"],
            ["diagnostic_tests.id"],
            name=op.f("fk_bookings_test_id_diagnostic_tests"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_bookings_user_id_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bookings")),
    )
    op.create_index(
        "ix_bookings_status_created_at", "bookings", ["status", "created_at"], unique=False
    )
    op.create_index(
        "ix_bookings_user_id_created_at", "bookings", ["user_id", "created_at"], unique=False
    )
    op.create_index(
        "uq_bookings_active_slot",
        "bookings",
        ["user_id", "centre_id", "test_id", "appointment_at"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'CONFIRMED')"),
    )
    op.execute("ALTER TABLE bookings ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_index(
        "uq_bookings_active_slot",
        table_name="bookings",
        postgresql_where=sa.text("status IN ('PENDING', 'CONFIRMED')"),
    )
    op.drop_index("ix_bookings_user_id_created_at", table_name="bookings")
    op.drop_index("ix_bookings_status_created_at", table_name="bookings")
    op.drop_table("bookings")
