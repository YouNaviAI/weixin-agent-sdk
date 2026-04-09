"""
weixin_agent_sdk.monitor.monitor 的测试用例。

覆盖：
  - interruptible_sleep：正常超时、stop_event 提前中断
  - monitor_weixin_provider：
      - SessionPausedError 处理（退避并继续）
      - API 层错误处理（连续 3 次退避）
      - transport 层错误处理（连续 3 次退避）
      - 正常消息分发（fire-and-forget Task）
      - longpolling_timeout_ms 动态传递
      - stop_event 中断循环
      - 退出前 gather 等待（STOP_GATHER_TIMEOUT_S）
      - gather 超时处理（STOP_GATHER_TIMEOUT_S 后放弃）
  - 压力测试：100 条消息分发
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from weixin_agent_sdk.api.session_guard import SessionPausedError
from weixin_agent_sdk.monitor.monitor import (
    BACKOFF_DELAY_MS,
    MAX_CONSECUTIVE_FAILURES,
    RETRY_DELAY_MS,
    STOP_GATHER_TIMEOUT_S,
    MonitorWeixinOpts,
    interruptible_sleep,
    monitor_weixin_provider,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def make_opts(
    *,
    agent=None,
    client=None,
    stop_event: asyncio.Event | None = None,
    account_id: str = "acct_test",
) -> MonitorWeixinOpts:
    if agent is None:
        agent = AsyncMock()
        from weixin_agent_sdk.agent import ChatResponse
        agent.chat = AsyncMock(return_value=ChatResponse(text="reply"))
    if client is None:
        client = make_client_stub()
    if stop_event is None:
        stop_event = asyncio.Event()
    cdn_session = AsyncMock()
    return MonitorWeixinOpts(
        account_id=account_id,
        agent=agent,
        client=client,
        cdn_base_url="https://cdn.example.com",
        cdn_session=cdn_session,
        stop_event=stop_event,
        log=lambda msg: None,
    )


def make_client_stub(
    *,
    updates_responses=None,
    session_guard=None,
):
    """
    构造 WeixinApiClient stub。
    updates_responses 是 get_updates 依次返回的值或异常列表。
    """
    client = AsyncMock()
    client.session_guard = session_guard or MagicMock(remaining_ms=lambda: 100)
    if updates_responses:
        client.get_updates = AsyncMock(side_effect=updates_responses)
    else:
        # 默认：第一次返回空消息，然后永远 StopIteration 让循环停止
        empty_resp = make_get_updates_resp([])
        client.get_updates = AsyncMock(side_effect=[empty_resp, asyncio.CancelledError()])
    client.send_message = AsyncMock(return_value=None)
    return client


def make_get_updates_resp(msgs=None, buf="buf_001", longpolling_timeout_ms=None, ret=0, errcode=0):
    """构造 GetUpdatesResp stub。"""
    resp = MagicMock()
    resp.msgs = msgs or []
    resp.get_updates_buf = buf
    resp.longpolling_timeout_ms = longpolling_timeout_ms
    resp.ret = ret
    resp.errcode = errcode
    resp.errmsg = None
    return resp


def make_weixin_message(from_user_id: str = "user1"):
    from weixin_agent_sdk.api.types import (
        MessageItem, MessageItemType, MessageState, MessageType, TextItem, WeixinMessage
    )
    return WeixinMessage(
        from_user_id=from_user_id,
        to_user_id="bot",
        client_id=f"cid_{from_user_id}",
        message_type=MessageType.USER,
        message_state=MessageState.FINISH,
        item_list=[MessageItem(type=MessageItemType.TEXT, text_item=TextItem(text="hi"))],
        context_token="tok_test",
    )


# ---------------------------------------------------------------------------
# interruptible_sleep
# ---------------------------------------------------------------------------

class TestInterruptibleSleep:
    @pytest.mark.asyncio
    async def test_completes_after_timeout(self):
        """stop_event 未设置时应等满超时时间。"""
        stop = asyncio.Event()
        import time
        start = time.monotonic()
        await interruptible_sleep(0.05, stop)
        elapsed = time.monotonic() - start
        assert elapsed >= 0.04

    @pytest.mark.asyncio
    async def test_interrupted_by_stop_event(self):
        """stop_event 被设置后应立即返回。"""
        stop = asyncio.Event()

        async def set_after_delay():
            await asyncio.sleep(0.02)
            stop.set()

        import time
        start = time.monotonic()
        asyncio.create_task(set_after_delay())
        await interruptible_sleep(10.0, stop)
        elapsed = time.monotonic() - start
        assert elapsed < 1.0, f"未被中断，等待了 {elapsed:.3f}s"

    @pytest.mark.asyncio
    async def test_already_set_returns_immediately(self):
        """stop_event 已设置时应立即返回。"""
        stop = asyncio.Event()
        stop.set()
        import time
        start = time.monotonic()
        await interruptible_sleep(10.0, stop)
        elapsed = time.monotonic() - start
        assert elapsed < 0.1

    @pytest.mark.asyncio
    async def test_zero_sleep(self):
        """0 秒超时应立即返回。"""
        stop = asyncio.Event()
        await interruptible_sleep(0.0, stop)


# ---------------------------------------------------------------------------
# monitor — stop_event 中断
# ---------------------------------------------------------------------------

class TestMonitorStopEvent:
    @pytest.mark.asyncio
    async def test_stop_event_exits_loop(self):
        """stop_event 设置后循环应退出。"""
        stop = asyncio.Event()

        # 第一次 get_updates 设置 stop_event，然后返回空消息
        empty_resp = make_get_updates_resp([])

        call_count = 0

        async def get_updates_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                stop.set()
            return empty_resp

        client = make_client_stub()
        client.get_updates = AsyncMock(side_effect=get_updates_side_effect)
        opts = make_opts(client=client, stop_event=stop)

        with patch("weixin_agent_sdk.monitor.monitor.WeixinConfigManager") as mock_mgr, \
             patch("weixin_agent_sdk.monitor.monitor.load_get_updates_buf", return_value=None), \
             patch("weixin_agent_sdk.monitor.monitor.save_get_updates_buf"), \
             patch("weixin_agent_sdk.monitor.monitor.get_sync_buf_file_path", return_value="/tmp/buf"):
            mock_mgr.return_value.get_for_user = AsyncMock(return_value=MagicMock(typing_ticket=None))
            await monitor_weixin_provider(opts)

        # 循环应在 stop_event 设置后很快退出
        assert call_count >= 1


# ---------------------------------------------------------------------------
# monitor — SessionPausedError 处理
# ---------------------------------------------------------------------------

class TestMonitorSessionPaused:
    @pytest.mark.asyncio
    async def test_session_paused_error_triggers_sleep(self):
        """SessionPausedError 应触发退避 sleep，随后继续尝试。"""
        stop = asyncio.Event()
        call_count = 0

        async def get_updates_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise SessionPausedError("session expired")
            stop.set()
            return make_get_updates_resp([])

        client = make_client_stub()
        client.get_updates = AsyncMock(side_effect=get_updates_side_effect)
        opts = make_opts(client=client, stop_event=stop)

        sleep_calls = []

        async def mock_sleep(seconds, _stop_event):
            sleep_calls.append(seconds)
            _stop_event.set()  # 防止死循环

        with patch("weixin_agent_sdk.monitor.monitor.interruptible_sleep", side_effect=mock_sleep), \
             patch("weixin_agent_sdk.monitor.monitor.WeixinConfigManager") as mock_mgr, \
             patch("weixin_agent_sdk.monitor.monitor.load_get_updates_buf", return_value=None), \
             patch("weixin_agent_sdk.monitor.monitor.save_get_updates_buf"), \
             patch("weixin_agent_sdk.monitor.monitor.get_sync_buf_file_path", return_value="/tmp/buf"):
            mock_mgr.return_value.get_for_user = AsyncMock(return_value=MagicMock(typing_ticket=None))
            await monitor_weixin_provider(opts)

        assert len(sleep_calls) >= 1
        # SessionPausedError 使用 remaining_ms / 1000 秒退避
        assert sleep_calls[0] > 0


# ---------------------------------------------------------------------------
# monitor — API 层错误处理
# ---------------------------------------------------------------------------

class TestMonitorApiError:
    @pytest.mark.asyncio
    async def test_api_error_triggers_retry_delay(self):
        """API 层错误（ret != 0）应触发 RETRY_DELAY_MS 退避。"""
        stop = asyncio.Event()
        call_count = 0

        async def get_updates_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return make_get_updates_resp([], ret=1, errcode=0)
            stop.set()
            return make_get_updates_resp([])

        client = make_client_stub()
        client.get_updates = AsyncMock(side_effect=get_updates_side_effect)
        opts = make_opts(client=client, stop_event=stop)

        sleep_calls = []

        async def mock_sleep(seconds, _stop_event):
            sleep_calls.append(seconds)
            _stop_event.set()

        with patch("weixin_agent_sdk.monitor.monitor.interruptible_sleep", side_effect=mock_sleep), \
             patch("weixin_agent_sdk.monitor.monitor.WeixinConfigManager") as mock_mgr, \
             patch("weixin_agent_sdk.monitor.monitor.load_get_updates_buf", return_value=None), \
             patch("weixin_agent_sdk.monitor.monitor.save_get_updates_buf"), \
             patch("weixin_agent_sdk.monitor.monitor.get_sync_buf_file_path", return_value="/tmp/buf"):
            mock_mgr.return_value.get_for_user = AsyncMock(return_value=MagicMock(typing_ticket=None))
            await monitor_weixin_provider(opts)

        assert len(sleep_calls) >= 1
        # 单次错误使用 RETRY_DELAY_MS / 1000
        assert abs(sleep_calls[0] - RETRY_DELAY_MS / 1000) < 0.1

    @pytest.mark.asyncio
    async def test_three_consecutive_api_errors_trigger_backoff(self):
        """3 次连续 API 错误应触发 BACKOFF_DELAY_MS 退避。"""
        stop = asyncio.Event()
        call_count = 0

        async def get_updates_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= MAX_CONSECUTIVE_FAILURES:
                return make_get_updates_resp([], ret=1)
            stop.set()
            return make_get_updates_resp([])

        client = make_client_stub()
        client.get_updates = AsyncMock(side_effect=get_updates_side_effect)
        opts = make_opts(client=client, stop_event=stop)

        sleep_durations = []

        async def mock_sleep(seconds, _stop_event):
            sleep_durations.append(seconds)
            # 只在 backoff 时设置 stop
            if seconds >= BACKOFF_DELAY_MS / 1000 - 1:
                _stop_event.set()

        with patch("weixin_agent_sdk.monitor.monitor.interruptible_sleep", side_effect=mock_sleep), \
             patch("weixin_agent_sdk.monitor.monitor.WeixinConfigManager") as mock_mgr, \
             patch("weixin_agent_sdk.monitor.monitor.load_get_updates_buf", return_value=None), \
             patch("weixin_agent_sdk.monitor.monitor.save_get_updates_buf"), \
             patch("weixin_agent_sdk.monitor.monitor.get_sync_buf_file_path", return_value="/tmp/buf"):
            mock_mgr.return_value.get_for_user = AsyncMock(return_value=MagicMock(typing_ticket=None))
            await monitor_weixin_provider(opts)

        # 应有一次 backoff 延迟
        assert any(abs(s - BACKOFF_DELAY_MS / 1000) < 1.0 for s in sleep_durations)


# ---------------------------------------------------------------------------
# monitor — transport 层错误处理
# ---------------------------------------------------------------------------

class TestMonitorTransportError:
    @pytest.mark.asyncio
    async def test_transport_error_triggers_retry(self):
        """网络异常应触发 RETRY_DELAY_MS 退避。"""
        stop = asyncio.Event()
        call_count = 0

        async def get_updates_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("network down")
            stop.set()
            return make_get_updates_resp([])

        client = make_client_stub()
        client.get_updates = AsyncMock(side_effect=get_updates_side_effect)
        opts = make_opts(client=client, stop_event=stop)

        sleep_calls = []

        async def mock_sleep(seconds, _stop_event):
            sleep_calls.append(seconds)
            _stop_event.set()

        with patch("weixin_agent_sdk.monitor.monitor.interruptible_sleep", side_effect=mock_sleep), \
             patch("weixin_agent_sdk.monitor.monitor.WeixinConfigManager") as mock_mgr, \
             patch("weixin_agent_sdk.monitor.monitor.load_get_updates_buf", return_value=None), \
             patch("weixin_agent_sdk.monitor.monitor.save_get_updates_buf"), \
             patch("weixin_agent_sdk.monitor.monitor.get_sync_buf_file_path", return_value="/tmp/buf"):
            mock_mgr.return_value.get_for_user = AsyncMock(return_value=MagicMock(typing_ticket=None))
            await monitor_weixin_provider(opts)

        assert len(sleep_calls) >= 1


# ---------------------------------------------------------------------------
# monitor — longpolling_timeout_ms 传递
# ---------------------------------------------------------------------------

class TestMonitorLongPollingTimeout:
    @pytest.mark.asyncio
    async def test_longpolling_timeout_passed_to_get_updates(self):
        """响应中的 longpolling_timeout_ms 应在下一次请求时作为 timeout_ms_override 传入。"""
        stop = asyncio.Event()
        call_count = 0
        received_timeouts = []

        async def get_updates_side_effect(buf="", timeout_ms_override=None):
            nonlocal call_count
            call_count += 1
            received_timeouts.append(timeout_ms_override)
            if call_count == 1:
                return make_get_updates_resp([], longpolling_timeout_ms=45_000)
            stop.set()
            return make_get_updates_resp([])

        client = make_client_stub()
        client.get_updates = AsyncMock(side_effect=get_updates_side_effect)
        opts = make_opts(client=client, stop_event=stop)

        with patch("weixin_agent_sdk.monitor.monitor.WeixinConfigManager") as mock_mgr, \
             patch("weixin_agent_sdk.monitor.monitor.load_get_updates_buf", return_value=None), \
             patch("weixin_agent_sdk.monitor.monitor.save_get_updates_buf"), \
             patch("weixin_agent_sdk.monitor.monitor.get_sync_buf_file_path", return_value="/tmp/buf"):
            mock_mgr.return_value.get_for_user = AsyncMock(return_value=MagicMock(typing_ticket=None))
            await monitor_weixin_provider(opts)

        # 第二次调用应传入服务端建议的超时
        assert len(received_timeouts) >= 2
        assert received_timeouts[1] == 45_000


# ---------------------------------------------------------------------------
# monitor — 消息分发
# ---------------------------------------------------------------------------

class TestMonitorMessageDispatch:
    @pytest.mark.asyncio
    async def test_message_dispatched_as_task(self):
        """收到消息时应创建 asyncio.Task 处理，不阻塞主循环。"""
        stop = asyncio.Event()
        processed = []

        async def fake_process_one(msg, deps):
            processed.append(msg.from_user_id)

        call_count = 0

        async def get_updates_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                msg = make_weixin_message("user_dispatch")
                return make_get_updates_resp([msg])
            stop.set()
            return make_get_updates_resp([])

        client = make_client_stub()
        client.get_updates = AsyncMock(side_effect=get_updates_side_effect)
        opts = make_opts(client=client, stop_event=stop)

        with patch("weixin_agent_sdk.monitor.monitor.process_one_message", side_effect=fake_process_one), \
             patch("weixin_agent_sdk.monitor.monitor.WeixinConfigManager") as mock_mgr, \
             patch("weixin_agent_sdk.monitor.monitor.load_get_updates_buf", return_value=None), \
             patch("weixin_agent_sdk.monitor.monitor.save_get_updates_buf"), \
             patch("weixin_agent_sdk.monitor.monitor.get_sync_buf_file_path", return_value="/tmp/buf"):
            mock_mgr.return_value.get_for_user = AsyncMock(return_value=MagicMock(typing_ticket=None))
            await monitor_weixin_provider(opts)

        assert "user_dispatch" in processed

    @pytest.mark.asyncio
    async def test_buf_persisted(self):
        """get_updates 返回新 buf 时应持久化。"""
        stop = asyncio.Event()
        saved_bufs = []

        call_count = 0

        async def get_updates_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return make_get_updates_resp([], buf="new_buf_123")
            stop.set()
            return make_get_updates_resp([])

        client = make_client_stub()
        client.get_updates = AsyncMock(side_effect=get_updates_side_effect)
        opts = make_opts(client=client, stop_event=stop)

        with patch("weixin_agent_sdk.monitor.monitor.WeixinConfigManager") as mock_mgr, \
             patch("weixin_agent_sdk.monitor.monitor.load_get_updates_buf", return_value=None), \
             patch("weixin_agent_sdk.monitor.monitor.save_get_updates_buf", side_effect=lambda p, b: saved_bufs.append(b)), \
             patch("weixin_agent_sdk.monitor.monitor.get_sync_buf_file_path", return_value="/tmp/buf"):
            mock_mgr.return_value.get_for_user = AsyncMock(return_value=MagicMock(typing_ticket=None))
            await monitor_weixin_provider(opts)

        assert "new_buf_123" in saved_bufs


# ---------------------------------------------------------------------------
# monitor — gather 超时
# ---------------------------------------------------------------------------

class TestMonitorGatherTimeout:
    @pytest.mark.asyncio
    async def test_gather_timeout_does_not_raise(self):
        """退出时 gather 超时应静默处理，不向上抛出。"""
        stop = asyncio.Event()

        async def slow_process(msg, deps):
            await asyncio.sleep(100)  # 模拟极慢的处理

        call_count = 0

        async def get_updates_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                stop.set()
                return make_get_updates_resp([make_weixin_message()])
            return make_get_updates_resp([])

        client = make_client_stub()
        client.get_updates = AsyncMock(side_effect=get_updates_side_effect)
        opts = make_opts(client=client, stop_event=stop)

        with patch("weixin_agent_sdk.monitor.monitor.process_one_message", side_effect=slow_process), \
             patch("weixin_agent_sdk.monitor.monitor.STOP_GATHER_TIMEOUT_S", 0.05), \
             patch("weixin_agent_sdk.monitor.monitor.WeixinConfigManager") as mock_mgr, \
             patch("weixin_agent_sdk.monitor.monitor.load_get_updates_buf", return_value=None), \
             patch("weixin_agent_sdk.monitor.monitor.save_get_updates_buf"), \
             patch("weixin_agent_sdk.monitor.monitor.get_sync_buf_file_path", return_value="/tmp/buf"):
            mock_mgr.return_value.get_for_user = AsyncMock(return_value=MagicMock(typing_ticket=None))
            # 不应抛出
            await monitor_weixin_provider(opts)


# ---------------------------------------------------------------------------
# 压力测试：100 条消息
# ---------------------------------------------------------------------------

class TestMonitorStress:
    @pytest.mark.asyncio
    async def test_100_messages_dispatched(self):
        """单次 getUpdates 返回 100 条消息，全部应在 2s 内分发完成。"""
        import time
        stop = asyncio.Event()
        processed = []

        async def fake_process(msg, deps):
            processed.append(msg.from_user_id)

        call_count = 0

        async def get_updates_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                msgs = [make_weixin_message(f"user_{i}") for i in range(100)]
                return make_get_updates_resp(msgs)
            stop.set()
            return make_get_updates_resp([])

        client = make_client_stub()
        client.get_updates = AsyncMock(side_effect=get_updates_side_effect)
        opts = make_opts(client=client, stop_event=stop)

        start = time.monotonic()
        with patch("weixin_agent_sdk.monitor.monitor.process_one_message", side_effect=fake_process), \
             patch("weixin_agent_sdk.monitor.monitor.WeixinConfigManager") as mock_mgr, \
             patch("weixin_agent_sdk.monitor.monitor.load_get_updates_buf", return_value=None), \
             patch("weixin_agent_sdk.monitor.monitor.save_get_updates_buf"), \
             patch("weixin_agent_sdk.monitor.monitor.get_sync_buf_file_path", return_value="/tmp/buf"):
            mock_mgr.return_value.get_for_user = AsyncMock(return_value=MagicMock(typing_ticket=None))
            await monitor_weixin_provider(opts)
        elapsed = time.monotonic() - start

        assert len(processed) == 100
        assert set(processed) == {f"user_{i}" for i in range(100)}
        assert elapsed < 2.0, f"100 条消息分发耗时 {elapsed:.3f}s 超过 2s"


# ---------------------------------------------------------------------------
# 错误路径补充：连续失败后成功应重置计数器
# ---------------------------------------------------------------------------

class TestMonitorConsecutiveFailureReset:
    @pytest.mark.asyncio
    async def test_success_resets_consecutive_failure_counter(self):
        """2 次失败后成功 1 次，再失败不触发 backoff（计数器已重置）。"""
        stop = asyncio.Event()
        call_count = 0
        sleep_durations = []

        async def get_updates_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count in (1, 2):
                return make_get_updates_resp([], ret=1)  # 失败
            if call_count == 3:
                return make_get_updates_resp([])  # 成功，重置计数
            if call_count == 4:
                return make_get_updates_resp([], ret=1)  # 再次失败（计数从 1 开始）
            stop.set()
            return make_get_updates_resp([])

        client = make_client_stub()
        client.get_updates = AsyncMock(side_effect=get_updates_side_effect)
        opts = make_opts(client=client, stop_event=stop)

        async def mock_sleep(seconds, _stop_event):
            sleep_durations.append(seconds)
            if _stop_event.is_set():
                return

        with patch("weixin_agent_sdk.monitor.monitor.interruptible_sleep", side_effect=mock_sleep), \
             patch("weixin_agent_sdk.monitor.monitor.WeixinConfigManager") as mock_mgr, \
             patch("weixin_agent_sdk.monitor.monitor.load_get_updates_buf", return_value=None), \
             patch("weixin_agent_sdk.monitor.monitor.save_get_updates_buf"), \
             patch("weixin_agent_sdk.monitor.monitor.get_sync_buf_file_path", return_value="/tmp/buf"):
            mock_mgr.return_value.get_for_user = AsyncMock(return_value=MagicMock(typing_ticket=None))
            await monitor_weixin_provider(opts)

        # 不应有 backoff 延迟（每次失败都只是 retry delay，从未连续 3 次）
        assert not any(abs(s - BACKOFF_DELAY_MS / 1000) < 1.0 for s in sleep_durations), \
            f"不应触发 backoff，但检测到 sleep={sleep_durations}"

    @pytest.mark.asyncio
    async def test_stop_event_during_transport_error_exits_cleanly(self):
        """transport 错误发生时 stop_event 已设置，循环应立即退出，不进入退避。"""
        stop = asyncio.Event()

        async def get_updates_side_effect(*args, **kwargs):
            stop.set()
            raise ConnectionError("network down while stopping")

        client = make_client_stub()
        client.get_updates = AsyncMock(side_effect=get_updates_side_effect)
        opts = make_opts(client=client, stop_event=stop)

        sleep_calls = []

        async def mock_sleep(seconds, _stop_event):
            sleep_calls.append(seconds)

        with patch("weixin_agent_sdk.monitor.monitor.interruptible_sleep", side_effect=mock_sleep), \
             patch("weixin_agent_sdk.monitor.monitor.WeixinConfigManager") as mock_mgr, \
             patch("weixin_agent_sdk.monitor.monitor.load_get_updates_buf", return_value=None), \
             patch("weixin_agent_sdk.monitor.monitor.save_get_updates_buf"), \
             patch("weixin_agent_sdk.monitor.monitor.get_sync_buf_file_path", return_value="/tmp/buf"):
            mock_mgr.return_value.get_for_user = AsyncMock(return_value=MagicMock(typing_ticket=None))
            await monitor_weixin_provider(opts)

        # stop_event 设置后应走 break 分支，不调用 interruptible_sleep
        assert sleep_calls == [], f"stop 后不应有 sleep，但有: {sleep_calls}"
