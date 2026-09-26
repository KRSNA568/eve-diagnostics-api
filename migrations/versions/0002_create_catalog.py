"""create catalog

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-26 13:28:05.561841
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "diagnostic_centres",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("address", sa.String(length=500), nullable=False),
        sa.Column("city", sa.String(length=100), nullable=False),
        sa.Column("pincode", sa.String(length=6), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
        sa.CheckConstraint(
            "pincode ~ '^[1-9][0-9]{5}$'", name=op.f("ck_diagnostic_centres_pincode_format")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_diagnostic_centres")),
    )
    op.create_index(
        "ix_diagnostic_centres_city_lower",
        "diagnostic_centres",
        [sa.literal_column("lower(city)")],
        unique=False,
    )
    op.create_table(
        "diagnostic_tests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
        sa.CheckConstraint("code = upper(code)", name=op.f("ck_diagnostic_tests_code_uppercase")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_diagnostic_tests")),
        sa.UniqueConstraint("code", name=op.f("uq_diagnostic_tests_code")),
    )
    op.create_table(
        "centre_tests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("centre_id", sa.Uuid(), nullable=False),
        sa.Column("test_id", sa.Uuid(), nullable=False),
        sa.Column("price", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="INR", nullable=False),
        sa.Column("is_available", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$'", name=op.f("ck_centre_tests_currency_iso4217")
        ),
        sa.CheckConstraint("price > 0", name=op.f("ck_centre_tests_price_positive")),
        sa.ForeignKeyConstraint(
            ["centre_id"],
            ["diagnostic_centres.id"],
            name=op.f("fk_centre_tests_centre_id_diagnostic_centres"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["test_id"],
            ["diagnostic_tests.id"],
            name=op.f("fk_centre_tests_test_id_diagnostic_tests"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_centre_tests")),
        sa.UniqueConstraint("centre_id", "test_id", name=op.f("uq_centre_tests_centre_id_test_id")),
    )
    op.create_index(op.f("ix_centre_tests_test_id"), "centre_tests", ["test_id"], unique=False)
    for table in ("diagnostic_centres", "diagnostic_tests", "centre_tests"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_index(op.f("ix_centre_tests_test_id"), table_name="centre_tests")
    op.drop_table("centre_tests")
    op.drop_table("diagnostic_tests")
    op.drop_index("ix_diagnostic_centres_city_lower", table_name="diagnostic_centres")
    op.drop_table("diagnostic_centres")
