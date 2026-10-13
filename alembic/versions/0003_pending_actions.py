"""持久化用户确认动作；基础 SQLite 启动也会 create_all。"""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "pending_actions",
        sa.Column("action_id", sa.String(64), primary_key=True),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("proposal_json", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
    )
    op.create_index("ix_pending_actions_session_id", "pending_actions", ["session_id"])


def downgrade():
    op.drop_table("pending_actions")
