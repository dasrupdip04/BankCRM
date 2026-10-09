"""Add an explicit account type."""
from alembic import op
import sqlalchemy as sa

revision = "0002_account_type"
down_revision = "0001_core"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("accounts", sa.Column("account_type", sa.String(24), nullable=False, server_default="checking"))

def downgrade():
    op.drop_column("accounts", "account_type")
