"""本次新增验收：通过实际 API 和 SQLite 复核安全闭环。"""

import pytest
from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.main import create_app


@pytest.fixture
def client():
    settings = Settings(
        app_env="testing",
        database_url="sqlite+aiosqlite:///:memory:",
        runtime_mode="demo",
        seed_demo=True,
        rate_limit_per_minute=1000,
    )
    with TestClient(create_app(settings)) as c:
        yield c


def ask(client, message, session_id=None, user_id="u_demo", **kwargs):
    return client.post(
        "/api/v1/chat",
        json={"message": message, "session_id": session_id, "user_id": user_id, **kwargs},
    )


def test_direct_policy_complex_unknown_and_source(client):
    direct = ask(client, "你好").json()
    assert direct["strategy"] == "direct"
    assert direct["diagnostics"]["evidence"] == []
    single = ask(client, "七天无理由退货需要满足什么条件？").json()
    assert single["strategy"] == "single"
    assert not single["refused"]
    assert single["citations"]
    assert "签收次日起 7 个自然日内" in single["answer"]
    for c in single["citations"]:
        source = client.get("/api/v1/sources/" + c["chunk_id"]).json()
        assert c["excerpt"] == source["content"]
        assert c["source"] == source["metadata"]["source"]
        assert c["excerpt"] in single["answer"]
    complex_ = ask(client, "定制马克杯过了7天但是有质量问题，还能退吗，运费谁承担？").json()
    assert complex_["strategy"] == "multi"
    assert complex_["diagnostics"]["retrieval"]
    assert complex_["diagnostics"]["stop_reason"]
    assert complex_["diagnostics"]["retrieval"][-1]["grade"]["sufficient"]
    unknown = ask(client, "支持火星配送吗？").json()
    assert unknown["refused"] and not unknown["citations"]


def test_order_logistics_followup_and_isolation(client):
    order = ask(client, "查询订单 ORD10001").json()
    assert "shipped" in order["answer"] and order["strategy"] == "agent"
    follow = ask(client, "它的物流到哪了？", order["session_id"]).json()
    assert "SF1234567890" in follow["answer"]
    assert "ORD10001" in follow["diagnostics"]["resolved_query"]
    forbidden = ask(client, "它的物流", order["session_id"], user_id="other")
    assert forbidden.status_code == 401
    fresh = ask(client, "它的物流到哪了？").json()
    assert "ORD10001" not in fresh["diagnostics"].get("resolved_query", "")


def test_cancel_requires_stored_confirmation_and_idempotent(client):
    first = ask(client, "帮我取消订单 ORD10003").json()
    proposal = first["pending_action"]
    assert proposal and "cancelled" not in first["answer"]
    before = ask(client, "查订单 ORD10003").json()
    assert "paid" in before["answer"]
    sid = first["session_id"]
    invalid = ask(client, "确认", sid, confirm_action_id="invented")
    assert invalid.status_code == 400
    wrong = ask(client, "确认", confirm_action_id=proposal["action_id"])
    assert wrong.status_code == 400
    # 当前输入即使换订单，也只能执行已经展示的动作。
    confirm = ask(client, "取消订单 ORD10001", sid, confirm_action_id=proposal["action_id"]).json()
    assert "ORD10003" in confirm["answer"] and "cancelled" in confirm["answer"]
    replay = ask(client, "确认", sid, confirm_action_id=proposal["action_id"]).json()
    assert replay["answer"] == confirm["answer"]
    assert replay["diagnostics"]["idempotent_replay"]
    assert replay["diagnostics"]["llm_calls"] == 0
    untouched = ask(client, "查订单 ORD10001").json()
    assert "shipped" in untouched["answer"]


def test_refund_repeat_and_concurrent_confirmation(client):
    first = ask(client, "给 ORD10002 申请退款，质量问题").json()
    proposal = first["pending_action"]
    assert proposal["tool_name"] == "create_refund_request"
    sid = first["session_id"]
    from concurrent.futures import ThreadPoolExecutor

    def confirm():
        return ask(client, "确认", sid, confirm_action_id=proposal["action_id"]).json()

    with ThreadPoolExecutor(2) as pool:
        outputs = list(pool.map(lambda _: confirm(), range(2)))
    assert outputs[0]["answer"] == outputs[1]["answer"]
    status = ask(client, "查询 ORD10002 退款进度").json()
    refunds = status["diagnostics"]["tools"][0]["output"]["refunds"]
    assert len(refunds) == 1 and refunds[0]["status"] == "requested"


def test_frontend_and_runtime_are_real_backend(client):
    assert "演示商店客服" in client.get("/").text
    assert client.get("/static/chat.js").status_code == 200
    runtime = client.get("/api/v1/runtime").json()
    assert runtime["mode"] == "demo"
    assert runtime["embedding"] == "hash" and runtime["reranker"] == "lexical"
    assert client.get("/api/v1/sources/unknown").status_code == 404


def test_retrieval_failure_is_visible(client, monkeypatch):
    service = client.app.state.container.chat_service

    async def broken(*args, **kwargs):
        from app.core.exceptions import RetrievalError

        raise RetrievalError("test injected failure")

    monkeypatch.setattr(service._retriever, "retrieve", broken)
    body = ask(client, "七天无理由退货需要满足什么条件？")
    assert body.status_code == 200
    result = body.json()
    assert result["refused"]
    assert result["runtime"]["mode"] == "degraded"
    assert "检索" in " ".join(result["runtime"]["warnings"])


def test_model_failure_has_degraded_status_and_actual_call_count(client):
    class Broken:
        async def generate(self, *args, **kwargs):
            from app.core.exceptions import LLMProviderError

            raise LLMProviderError("injected model outage")

    service = client.app.state.container.chat_service
    service._llm.provider = Broken()
    result = ask(client, "七天无理由退货需要满足什么条件？").json()
    assert result["refused"]
    assert result["runtime"]["mode"] == "degraded"
    assert result["diagnostics"]["llm_calls"] >= 2
    assert all("error" in c for c in result["diagnostics"]["model_calls"])
    assert result["diagnostics"]["stop_reason"] == "grader_unavailable"


def test_multi_turn_policy_context(client):
    first = ask(client, "定制印字商品适用无理由退货吗？").json()
    follow = ask(client, "那如果有质量问题呢？", first["session_id"]).json()
    assert "定制" in follow["diagnostics"]["resolved_query"]
    assert follow["strategy"] == "multi"


def test_expired_action_is_rejected(client):
    from datetime import timedelta

    from app.core.time import utcnow
    from app.repositories.actions import PendingActionRow

    first = ask(client, "帮我取消订单 ORD10003").json()
    action = first["pending_action"]["action_id"]

    async def expire():
        async with client.app.state.container.session_factory() as db:
            row = await db.get(PendingActionRow, action)
            row.expires_at = utcnow() - timedelta(seconds=1)
            await db.commit()

    client.portal.call(expire)
    response = ask(client, "确认", first["session_id"], confirm_action_id=action)
    assert response.status_code == 400
    assert "过期" in response.json()["message"]


def test_saved_summary_is_loaded_for_next_request(client):
    from app.repositories.models import SessionRow
    from app.schemas.chat import ChatRequest

    first = ask(client, "你好").json()
    sid = first["session_id"]

    async def check():
        async with client.app.state.container.session_factory() as db:
            row = await db.get(SessionRow, sid)
            row.summary = "用户咨询定制商品的质量售后"
            await db.commit()
        async with client.app.state.container.session_factory() as db:
            state = await client.app.state.container.chat_service._initial_state(
                db,
                ChatRequest(message="继续", user_id="u_demo", session_id=sid),
                sid,
                "test_summary",
            )
            return state["summary"]

    assert client.portal.call(check) == "用户咨询定制商品的质量售后"
