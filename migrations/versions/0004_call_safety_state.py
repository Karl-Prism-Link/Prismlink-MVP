"""persist immediate voice safety state on active calls

Revision ID: 0004_call_safety_state
Revises: 0003_tenant_business_profile
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_call_safety_state"
down_revision = "0003_tenant_business_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "calls",
        sa.Column("immediate_safety_active", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column("calls", sa.Column("safety_reason", sa.Text(), nullable=True))
    op.add_column(
        "calls",
        sa.Column("safety_response_sent", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("calls", "safety_response_sent")
    op.drop_column("calls", "safety_reason")
    op.drop_column("calls", "immediate_safety_active")
