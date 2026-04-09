"""
process_message 补充测试：边界值、错误路径、并发陷阱、时序保证。
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from weixin_agent_sdk.api.types import (
    MessageItem,
    MessageItemType,
    MessageState,
    MessageType,
    TextItem,
    WeixinMessage,
)
from weixin_agent_sdk.messaging.inbound import context_token_store


# ---------------------------------------------------------------------------
# 共用工具（重新定义，避免跨文件依赖）
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_token_store():
    context_token_store.clear()
    yield
    context_token_store.clear()


def _make_message(items=None, context_token="tok_test", from_user_id="user1"):
    return WeixinMessage(
        from_user_id=from_user_id,
        to_user_id="bot",
        client_id="cid",
        message_type=MessageType.USER,
        message_state=MessageState.FINISH,
        item_list=items or [],
        context_token=context_token,
        create_time_ms="1700000000000",
    )


def _make_deps(agent=None, typing_ticket=None):
    client = AsyncMock()
    client.send_message = AsyncMock(return_value=None)
    client.send_typing = AsyncMock(return_value=None)
    cdn_session = AsyncMock()
    if agent is None:
        from weixin_agent_sdk.agent import ChatResponse
        agent = AsyncMock()
        agent.chat = AsyncMock(return_value=ChatResponse(text="ok"))
    from weixin_agent_sdk.messaging.process_message import ProcessMessageDeps
    return ProcessMessageDeps(
        account_id="acct1",
        agent=agent,
        client=client,
        cdn_base_url="https://cdn.example.com",
        cdn_session=cdn_session,
        typing_ticket=typing_ticket,
        log=lambda m: None,
        err_log=lambda m: None,
    )


def _text_item(text: str) -> MessageItem:
    return MessageItem(type=MessageItemType.TEXT, text_item=TextItem(text=text))


# ---------------------------------------------------------------------------
# 边界值
# ---------------------------------------------------------------------------

class TestEdgeValues:
    @pytest.mark.asyncio
    async def test_empty_from_user_id_sends_to_empty_string(self):
        """from_user_id 为 "" 时 to="" 应被正常传给 send_message_weixin，不崩溃。"""
        from weixin_agent_sdk.messaging.process_message import process_one_message
        from weixin_agent_sdk.agent import ChatResponse
        msg = _make_message([_text_item("hi")], from_user_id="")
        agent = AsyncMock()
        agent.chat = AsyncMock(return_value=ChatResponse(text="reply"))
        deps = _make_deps(agent=agent)
        captured_to = []

        async def mock_send(to, text, client, context_token):
            captured_to.append(to)

        with patch(
            "weixin_agent_sdk.messaging.process_message.send_message_weixin",
            side_effect=mock_send,
        ):
            await process_one_message(msg, deps)

        assert captured_to == [""]

    @pytest.mark.asyncio
    async def test_empty_item_list_does_not_crash(self):
        """item_list=[] 时流水线应静默跳过，不抛出。"""
        from weixin_agent_sdk.messaging.process_message import process_one_message
        from weixin_agent_sdk.agent import ChatResponse
        msg = _make_message([])
        agent = AsyncMock()
        agent.chat = AsyncMock(return_value=ChatResponse(text=""))
        deps = _make_deps(agent=agent)
        await process_one_message(msg, deps)

    @pytest.mark.asyncio
    async def test_whitespace_context_token_stored_as_is(self):
        """
        仅含空白的 context_token（"   "）是 truthy，当前实现会存入。
        此测试记录并固定该行为，防止意外变化。
        """
        from weixin_agent_sdk.messaging.process_message import process_one_message
        from weixin_agent_sdk.messaging.inbound import get_context_token
        msg = _make_message([_text_item("hello")], context_token="   ")
        deps = _make_deps()
        with patch(
            "weixin_agent_sdk.messaging.process_message.send_message_weixin",
            new_callable=AsyncMock,
        ):
            await process_one_message(msg, deps)
        assert get_context_token("acct1", "user1") == "   "

    @pytest.mark.asyncio
    async def test_none_context_token_not_stored(self):
        """context_token=None 时不应写入 store，也不向上抛出。"""
        from weixin_agent_sdk.messaging.process_message import process_one_message
        from weixin_agent_sdk.messaging.inbound import get_context_token
        msg = _make_message([_text_item("hello")], context_token=None)
        deps = _make_deps()
        with patch(
            "weixin_agent_sdk.messaging.process_message.send_message_weixin",
            new_callable=AsyncMock,
        ):
            await process_one_message(msg, deps)
        assert get_context_token("acct1", "user1") is None


# ---------------------------------------------------------------------------
# 错误路径
# ---------------------------------------------------------------------------

class TestErrorPaths:
    @pytest.mark.asyncio
    async def test_error_notice_internal_send_failure_does_not_propagate(self):
        """
        error_notice 内部的 send_message_weixin 失败时，
        send_weixin_error_notice 自身应静默吞掉（其内部有 try/except），
        不向 process_one_message 传播。
        """
        from weixin_agent_sdk.messaging.process_message import process_one_message
        msg = _make_message([_text_item("hi")])
        agent = AsyncMock()
        agent.chat = AsyncMock(side_effect=RuntimeError("agent down"))
        deps = _make_deps(agent=agent)
        # 让 send_message_weixin 在 error_notice 内部抛出
        with patch(
            "weixin_agent_sdk.messaging.error_notice.send_message_weixin",
            new_callable=AsyncMock,
            side_effect=ConnectionError("inner send failed"),
        ):
            await process_one_message(msg, deps)  # 不应抛出

    @pytest.mark.asyncio
    async def test_multiple_independent_failures_each_notified(self):
        """两条不同用户消息的 agent 都失败，各自独立发送错误通知。"""
        from weixin_agent_sdk.messaging.process_message import process_one_message
        notified_users = []

        async def fake_notice(to, context_token, message, client):
            notified_users.append(to)

        for uid in ["alice", "bob"]:
            msg = _make_message([_text_item("hi")], from_user_id=uid)
            agent = AsyncMock()
            agent.chat = AsyncMock(side_effect=ValueError("fail"))
            deps = _make_deps(agent=agent)
            with patch(
                "weixin_agent_sdk.messaging.process_message.send_weixin_error_notice",
                side_effect=fake_notice,
            ):
                await process_one_message(msg, deps)

        assert notified_users == ["alice", "bob"]

    @pytest.mark.asyncio
    async def test_send_reply_failure_does_not_propagate(self):
        """发送回复失败时错误通知依然被触发，不向外抛出。"""
        from weixin_agent_sdk.messaging.process_message import process_one_message
        from weixin_agent_sdk.agent import ChatResponse
        msg = _make_message([_text_item("hi")])
        agent = AsyncMock()
        agent.chat = AsyncMock(return_value=ChatResponse(text="reply"))
        deps = _make_deps(agent=agent)
        with patch(
            "weixin_agent_sdk.messaging.process_message.send_message_weixin",
            new_callable=AsyncMock,
            side_effect=RuntimeError("send failed"),
        ), patch(
            "weixin_agent_sdk.messaging.process_message.send_weixin_error_notice",
            new_callable=AsyncMock,
        ) as mock_notice:
            await process_one_message(msg, deps)

        mock_notice.assert_called_once()


# ---------------------------------------------------------------------------
# 并发陷阱：fire_and_forget GC 保护
# ---------------------------------------------------------------------------

class TestFireAndForget:
    @pytest.mark.asyncio
    async def test_task_held_in_background_tasks_until_done(self):
        """fire_and_forget 的任务在完成前应保留在 background_tasks 集合中。"""
        from weixin_agent_sdk.messaging.process_message import background_tasks, fire_and_forget

        gate = asyncio.Event()
        release = asyncio.Event()

        async def slow():
            gate.set()
            await release.wait()

        fire_and_forget(slow())
        await asyncio.wait_for(gate.wait(), timeout=1.0)

        assert len(background_tasks) >= 1, "任务应在完成前驻留在 background_tasks"

        release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert len(background_tasks) == 0, "任务完成后应从 background_tasks 移除"

    @pytest.mark.asyncio
    async def test_fire_and_forget_exception_consumed_silently(self):
        """fire_and_forget 中的异常不应变成 unhandled exception 警告。"""
        from weixin_agent_sdk.messaging.process_message import fire_and_forget

        async def failing():
            raise ValueError("intentional failure")

        fire_and_forget(failing())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        # 若有 unhandled exception，pytest-asyncio 会报 warning/error；无报错即通过


# ---------------------------------------------------------------------------
# 时序保证：typing 配对 + at-most-once
# ---------------------------------------------------------------------------

class TestTimingGuarantees:
    @pytest.mark.asyncio
    async def test_typing_cancel_always_sent_on_agent_error(self):
        """即使 agent 抛出异常，typing CANCEL 也必须在 finally 中发出。"""
        from weixin_agent_sdk.messaging.process_message import process_one_message
        from weixin_agent_sdk.api.types import TypingStatus

        msg = _make_message([_text_item("hi")])
        agent = AsyncMock()
        agent.chat = AsyncMock(side_effect=RuntimeError("crash"))
        deps = _make_deps(agent=agent, typing_ticket="tkt")

        with patch(
            "weixin_agent_sdk.messaging.process_message.send_weixin_error_notice",
            new_callable=AsyncMock,
        ):
            await process_one_message(msg, deps)

        # 等 fire-and-forget 完成
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        # 应调用两次：TYPING + CANCEL
        assert deps.client.send_typing.call_count == 2

    @pytest.mark.asyncio
    async def test_no_typing_calls_without_ticket(self):
        """无 typing_ticket 时 send_typing 的调用次数应为 0（严格 at-most-none）。"""
        from weixin_agent_sdk.messaging.process_message import process_one_message
        from weixin_agent_sdk.agent import ChatResponse
        msg = _make_message([_text_item("hi")])
        agent = AsyncMock()
        agent.chat = AsyncMock(return_value=ChatResponse(text="ok"))
        deps = _make_deps(agent=agent, typing_ticket=None)

        with patch(
            "weixin_agent_sdk.messaging.process_message.send_message_weixin",
            new_callable=AsyncMock,
        ):
            await process_one_message(msg, deps)

        await asyncio.sleep(0)
        deps.client.send_typing.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_items_sequential_unique_client_ids(self):
        """send_items 应顺序发送，每条 item 生成独立且唯一的 client_id。"""
        from weixin_agent_sdk.messaging.send import send_items

        sent_ids = []

        async def capture(msg):
            sent_ids.append(msg.client_id)

        client = AsyncMock()
        client.send_message = AsyncMock(side_effect=capture)

        items = [
            MessageItem(type=MessageItemType.TEXT, text_item=TextItem(text=f"item{i}"))
            for i in range(5)
        ]
        await send_items("user1", "tok", items, client, "test")

        assert len(sent_ids) == 5
        assert len(set(sent_ids)) == 5, "每条 item 应使用不同的 client_id"

    @pytest.mark.asyncio
    async def test_context_token_overwritten_by_latest_message(self):
        """同一用户连续两条消息携带不同 context_token，应以最后一条为准。"""
        from weixin_agent_sdk.messaging.process_message import process_one_message
        from weixin_agent_sdk.messaging.inbound import get_context_token
        from weixin_agent_sdk.agent import ChatResponse

        for tok in ["token_first", "token_second"]:
            msg = _make_message([_text_item("hi")], context_token=tok, from_user_id="user1")
            agent = AsyncMock()
            agent.chat = AsyncMock(return_value=ChatResponse(text=""))
            deps = _make_deps(agent=agent)
            with patch(
                "weixin_agent_sdk.messaging.process_message.send_message_weixin",
                new_callable=AsyncMock,
            ):
                await process_one_message(msg, deps)

        assert get_context_token("acct1", "user1") == "token_second"
