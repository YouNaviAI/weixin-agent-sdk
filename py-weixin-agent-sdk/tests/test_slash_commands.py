"""
weixin_agent_sdk.messaging.slash_commands 的测试用例。

覆盖：
  - handle_slash_command：/echo, /toggle-debug, /clear, 未知命令
  - send_reply：context_token 缺失时静默跳过
  - 命令大小写不敏感
  - 边界条件：空内容、非斜杠开头、命令执行异常
  - 压力测试：大量并发命令调用
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from weixin_agent_sdk.messaging.slash_commands import (
    SlashCommandContext,
    SlashCommandResult,
    handle_slash_command,
    send_reply,
)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

def make_ctx(
    *,
    context_token: str | None = "tok_test",
    on_clear=None,
    account_id: str = "acct1",
) -> SlashCommandContext:
    """构造 SlashCommandContext stub。"""
    client = AsyncMock()
    client.send_message = AsyncMock(return_value=None)
    logs = []
    return SlashCommandContext(
        to="user1",
        account_id=account_id,
        client=client,
        context_token=context_token,
        log=logs.append,
        err_log=logs.append,
        on_clear=on_clear,
    )


NOW_MS = 1_700_000_000_000.0  # 固定时间戳，避免测试依赖系统时间


# ---------------------------------------------------------------------------
# send_reply
# ---------------------------------------------------------------------------

class TestSendReply:
    @pytest.mark.asyncio
    async def test_sends_when_context_token_present(self):
        ctx = make_ctx()
        with patch(
            "weixin_agent_sdk.messaging.slash_commands.send_message_weixin",
            new_callable=AsyncMock,
        ) as mock_send:
            await send_reply(ctx, "hello")
            mock_send.assert_called_once_with("user1", "hello", ctx.client, "tok_test")

    @pytest.mark.asyncio
    async def test_skips_when_context_token_missing(self):
        ctx = make_ctx(context_token=None)
        with patch(
            "weixin_agent_sdk.messaging.slash_commands.send_message_weixin",
            new_callable=AsyncMock,
        ) as mock_send:
            await send_reply(ctx, "hello")
            mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_context_token_empty(self):
        ctx = make_ctx(context_token="")
        with patch(
            "weixin_agent_sdk.messaging.slash_commands.send_message_weixin",
            new_callable=AsyncMock,
        ) as mock_send:
            await send_reply(ctx, "hello")
            mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# handle_slash_command — 非命令输入
# ---------------------------------------------------------------------------

class TestHandleSlashCommandNonCommand:
    @pytest.mark.asyncio
    async def test_plain_text_not_handled(self):
        ctx = make_ctx()
        result = await handle_slash_command("hello world", ctx, NOW_MS)
        assert result.handled is False

    @pytest.mark.asyncio
    async def test_empty_string_not_handled(self):
        ctx = make_ctx()
        result = await handle_slash_command("", ctx, NOW_MS)
        assert result.handled is False

    @pytest.mark.asyncio
    async def test_unknown_slash_command_not_handled(self):
        ctx = make_ctx()
        result = await handle_slash_command("/unknown-cmd", ctx, NOW_MS)
        assert result.handled is False

    @pytest.mark.asyncio
    async def test_slash_only_not_handled(self):
        ctx = make_ctx()
        result = await handle_slash_command("/", ctx, NOW_MS)
        assert result.handled is False


# ---------------------------------------------------------------------------
# handle_slash_command — /echo
# ---------------------------------------------------------------------------

class TestEchoCommand:
    @pytest.mark.asyncio
    async def test_echo_handled(self):
        ctx = make_ctx()
        with patch(
            "weixin_agent_sdk.messaging.slash_commands.send_message_weixin",
            new_callable=AsyncMock,
        ):
            result = await handle_slash_command("/echo hello", ctx, NOW_MS)
        assert result.handled is True

    @pytest.mark.asyncio
    async def test_echo_sends_message_and_timing(self):
        ctx = make_ctx()
        sent_texts = []

        async def mock_send(to, text, client, token):
            sent_texts.append(text)

        with patch(
            "weixin_agent_sdk.messaging.slash_commands.send_message_weixin",
            side_effect=mock_send,
        ):
            await handle_slash_command("/echo test message", ctx, NOW_MS)

        assert any("test message" in t for t in sent_texts)
        assert any("通道耗时" in t for t in sent_texts)

    @pytest.mark.asyncio
    async def test_echo_without_args_sends_timing_only(self):
        """/echo 无参数时只发送耗时统计，不发送空消息。"""
        ctx = make_ctx()
        sent_texts = []

        async def mock_send(to, text, client, token):
            sent_texts.append(text)

        with patch(
            "weixin_agent_sdk.messaging.slash_commands.send_message_weixin",
            side_effect=mock_send,
        ):
            await handle_slash_command("/echo", ctx, NOW_MS)

        assert len(sent_texts) == 1  # 只有耗时，没有空消息
        assert "通道耗时" in sent_texts[0]

    @pytest.mark.asyncio
    async def test_echo_case_insensitive(self):
        ctx = make_ctx()
        with patch(
            "weixin_agent_sdk.messaging.slash_commands.send_message_weixin",
            new_callable=AsyncMock,
        ):
            result = await handle_slash_command("/ECHO hello", ctx, NOW_MS)
        assert result.handled is True

    @pytest.mark.asyncio
    async def test_echo_with_event_timestamp(self):
        """提供 event_timestamp_ms 时耗时信息不应为 N/A。"""
        ctx = make_ctx()
        sent_texts = []

        async def mock_send(to, text, client, token):
            sent_texts.append(text)

        event_ts = NOW_MS - 500  # 500ms 延迟
        with patch(
            "weixin_agent_sdk.messaging.slash_commands.send_message_weixin",
            side_effect=mock_send,
        ):
            await handle_slash_command("/echo", ctx, NOW_MS, event_timestamp_ms=event_ts)

        timing_text = next(t for t in sent_texts if "通道耗时" in t)
        assert "N/A" not in timing_text.split("平台")[1].split("\n")[0]  # 平台→插件不是 N/A


# ---------------------------------------------------------------------------
# handle_slash_command — /toggle-debug
# ---------------------------------------------------------------------------

class TestToggleDebugCommand:
    @pytest.mark.asyncio
    async def test_toggle_debug_handled(self):
        ctx = make_ctx()
        with patch(
            "weixin_agent_sdk.messaging.slash_commands.toggle_debug_mode",
            return_value=True,
        ), patch(
            "weixin_agent_sdk.messaging.slash_commands.send_message_weixin",
            new_callable=AsyncMock,
        ):
            result = await handle_slash_command("/toggle-debug", ctx, NOW_MS)
        assert result.handled is True

    @pytest.mark.asyncio
    async def test_toggle_debug_on_message(self):
        """toggle 返回 True 时应回复"已开启"。"""
        ctx = make_ctx()
        sent_texts = []

        async def mock_send(to, text, client, token):
            sent_texts.append(text)

        with patch(
            "weixin_agent_sdk.messaging.slash_commands.toggle_debug_mode",
            return_value=True,
        ), patch(
            "weixin_agent_sdk.messaging.slash_commands.send_message_weixin",
            side_effect=mock_send,
        ):
            await handle_slash_command("/toggle-debug", ctx, NOW_MS)

        assert any("开启" in t for t in sent_texts)

    @pytest.mark.asyncio
    async def test_toggle_debug_off_message(self):
        """toggle 返回 False 时应回复"已关闭"。"""
        ctx = make_ctx()
        sent_texts = []

        async def mock_send(to, text, client, token):
            sent_texts.append(text)

        with patch(
            "weixin_agent_sdk.messaging.slash_commands.toggle_debug_mode",
            return_value=False,
        ), patch(
            "weixin_agent_sdk.messaging.slash_commands.send_message_weixin",
            side_effect=mock_send,
        ):
            await handle_slash_command("/toggle-debug", ctx, NOW_MS)

        assert any("关闭" in t for t in sent_texts)

    @pytest.mark.asyncio
    async def test_toggle_debug_uses_account_id(self):
        """toggle_debug_mode 应传入正确的 account_id。"""
        ctx = make_ctx(account_id="my_account")
        captured = []

        def mock_toggle(account_id):
            captured.append(account_id)
            return True

        with patch(
            "weixin_agent_sdk.messaging.slash_commands.toggle_debug_mode",
            side_effect=mock_toggle,
        ), patch(
            "weixin_agent_sdk.messaging.slash_commands.send_message_weixin",
            new_callable=AsyncMock,
        ):
            await handle_slash_command("/toggle-debug", ctx, NOW_MS)

        assert captured == ["my_account"]


# ---------------------------------------------------------------------------
# handle_slash_command — /clear
# ---------------------------------------------------------------------------

class TestClearCommand:
    @pytest.mark.asyncio
    async def test_clear_handled(self):
        ctx = make_ctx()
        with patch(
            "weixin_agent_sdk.messaging.slash_commands.send_message_weixin",
            new_callable=AsyncMock,
        ):
            result = await handle_slash_command("/clear", ctx, NOW_MS)
        assert result.handled is True

    @pytest.mark.asyncio
    async def test_clear_calls_on_clear(self):
        cleared = []
        ctx = make_ctx(on_clear=lambda: cleared.append(True))
        with patch(
            "weixin_agent_sdk.messaging.slash_commands.send_message_weixin",
            new_callable=AsyncMock,
        ):
            await handle_slash_command("/clear", ctx, NOW_MS)
        assert cleared == [True]

    @pytest.mark.asyncio
    async def test_clear_without_on_clear_does_not_crash(self):
        """on_clear=None 时不应报错。"""
        ctx = make_ctx(on_clear=None)
        with patch(
            "weixin_agent_sdk.messaging.slash_commands.send_message_weixin",
            new_callable=AsyncMock,
        ):
            result = await handle_slash_command("/clear", ctx, NOW_MS)
        assert result.handled is True

    @pytest.mark.asyncio
    async def test_clear_sends_confirmation(self):
        ctx = make_ctx()
        sent_texts = []

        async def mock_send(to, text, client, token):
            sent_texts.append(text)

        with patch(
            "weixin_agent_sdk.messaging.slash_commands.send_message_weixin",
            side_effect=mock_send,
        ):
            await handle_slash_command("/clear", ctx, NOW_MS)

        assert any("清除" in t or "重新" in t for t in sent_texts)


# ---------------------------------------------------------------------------
# 命令执行异常处理
# ---------------------------------------------------------------------------

class TestCommandExceptionHandling:
    @pytest.mark.asyncio
    async def test_exception_in_echo_returns_handled_true(self):
        """命令执行异常应捕获并回复错误，不向上抛出。"""
        ctx = make_ctx()

        async def failing_send(*args, **kwargs):
            raise RuntimeError("send failed")

        with patch(
            "weixin_agent_sdk.messaging.slash_commands.send_message_weixin",
            side_effect=failing_send,
        ):
            result = await handle_slash_command("/echo test", ctx, NOW_MS)

        assert result.handled is True

    @pytest.mark.asyncio
    async def test_exception_in_toggle_returns_handled_true(self):
        ctx = make_ctx()

        with patch(
            "weixin_agent_sdk.messaging.slash_commands.toggle_debug_mode",
            side_effect=IOError("disk error"),
        ), patch(
            "weixin_agent_sdk.messaging.slash_commands.send_message_weixin",
            new_callable=AsyncMock,
        ):
            result = await handle_slash_command("/toggle-debug", ctx, NOW_MS)

        assert result.handled is True


# ---------------------------------------------------------------------------
# 压力测试
# ---------------------------------------------------------------------------

class TestSlashCommandStress:
    @pytest.mark.asyncio
    async def test_many_echo_commands_concurrently(self):
        """100 个并发 /echo 调用，全部应返回 handled=True，不竞争崩溃。"""
        async def run_one(i: int) -> SlashCommandResult:
            ctx = make_ctx()
            with patch(
                "weixin_agent_sdk.messaging.slash_commands.send_message_weixin",
                new_callable=AsyncMock,
            ):
                return await handle_slash_command(f"/echo msg{i}", ctx, NOW_MS)

        results = await asyncio.gather(*[run_one(i) for i in range(100)])
        assert all(r.handled for r in results)

    @pytest.mark.asyncio
    async def test_many_unknown_commands(self):
        """100 个未知命令，全部应返回 handled=False，且不调用 send。"""
        ctx = make_ctx()
        with patch(
            "weixin_agent_sdk.messaging.slash_commands.send_message_weixin",
            new_callable=AsyncMock,
        ) as mock_send:
            for i in range(100):
                result = await handle_slash_command(f"/unknown{i}", ctx, NOW_MS)
                assert result.handled is False
            mock_send.assert_not_called()
