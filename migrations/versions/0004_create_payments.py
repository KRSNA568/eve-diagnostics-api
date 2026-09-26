"""create payments

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-26 14:26:35.920699
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("booking_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_reference", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("failure_reason", sa.String(length=200), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f("ck_payments_currency_iso4217")),
        sa.CheckConstraint(
            "status IN ('PENDING', 'SUCCESS', 'FAILED')", name=op.f("ck_payments_status")
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_payments_amount_positive")),
        sa.ForeignKeyConstraint(
            ["booking_id"],
            ["bookings.id"],
            name=op.f("fk_payments_booking_id_bookings"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payments")),
        sa.UniqueConstraint("provider_reference", name=op.f("uq_payments_provider_reference")),
    )
    op.create_index(op.f("ix_payments_booking_id"), "payments", ["booking_id"], unique=False)
    op.create_index(
        "uq_payments_booking_idempotency_key",
        "payments",
        ["booking_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "uq_payments_one_success_per_booking",
        "payments",
        ["booking_id"],
        unique=True,
        postgresql_where=sa.text("status = 'SUCCESS'"),
    )

    op.execute("ALTER TABLE payments ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_index(
        "uq_payments_one_success_per_booking",
        table_name="payments",
        postgresql_where=sa.text("status = 'SUCCESS'"),
    )
    op.drop_index(
        "uq_payments_booking_idempotency_key",
        table_name="payments",
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.drop_index(op.f("ix_payments_booking_id"), table_name="payments")
    op.drop_table("payments")
