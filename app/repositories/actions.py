"""确认绑定服务端存储的动作，用户输入不能重新决定确认参数。"""

import json
from datetime import datetime, timedelta

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.exceptions import InvalidRequestError
from app.core.time import utcnow
from app.repositories.models import Base
from app.schemas.chat import ActionProposal


class PendingActionRow(Base):
    __tablename__ = "pending_actions"
    action_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64))
    proposal_json: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)


async def save_action(db, proposal, session_id, user_id):
    db.add(
        PendingActionRow(
            action_id=proposal.action_id,
            session_id=session_id,
            user_id=user_id,
            proposal_json=proposal.model_dump_json(),
            expires_at=utcnow() + timedelta(minutes=15),
        )
    )


async def load_action(db, action_id, session_id, user_id):
    row = await db.get(PendingActionRow, action_id)
    if row is None or row.session_id != session_id or row.user_id != user_id:
        raise InvalidRequestError("确认动作不存在，或不属于当前会话。")
    # SQLite 读出的 datetime 无时区，按 UTC 比较。
    if row.result_json is None and row.expires_at.replace(tzinfo=None) < utcnow().replace(
        tzinfo=None
    ):
        raise InvalidRequestError("确认动作已过期，请重新提出申请。")
    return row, ActionProposal.model_validate(json.loads(row.proposal_json))
