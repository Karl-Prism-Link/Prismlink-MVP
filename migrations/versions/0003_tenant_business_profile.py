"""add salon business profile details

Revision ID: 0003_tenant_business_profile
Revises: 0002_service_price
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_tenant_business_profile"
down_revision = "0002_service_price"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("address", sa.String(length=300), nullable=True))
    op.add_column("tenants", sa.Column("contact_email", sa.String(length=320), nullable=True))
    op.add_column(
        "tenants", sa.Column("currency", sa.String(length=3), server_default="NZD", nullable=False)
    )
    op.add_column(
        "tenants",
        sa.Column("prices_include_gst", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.add_column("tenants", sa.Column("ai_disclosure", sa.Text(), nullable=True))
    op.add_column("tenants", sa.Column("walk_ins_policy", sa.Text(), nullable=True))
    op.add_column("tenants", sa.Column("parking_info", sa.Text(), nullable=True))
    op.add_column("tenants", sa.Column("accessibility_info", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tenants", "accessibility_info")
    op.drop_column("tenants", "parking_info")
    op.drop_column("tenants", "walk_ins_policy")
    op.drop_column("tenants", "ai_disclosure")
    op.drop_column("tenants", "prices_include_gst")
    op.drop_column("tenants", "currency")
    op.drop_column("tenants", "contact_email")
    op.drop_column("tenants", "address")
