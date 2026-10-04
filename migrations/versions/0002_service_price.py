"""add service price

Revision ID: 0002_service_price
Revises: 0001_initial
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_service_price"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("services", sa.Column("price", sa.Integer(), nullable=True))

def downgrade() -> None:
    op.drop_column("services", "price")
