"""
微信斜杠命令处理模块。

支持的命令：
  /echo <消息>       直接回复（不经 AI），附带通道耗时统计
  /toggle-debug      开关调试模式，启用后每条 AI 回复追加全链路耗时
  /clear             清除当前会话，重新开始对话
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from weixin_agent_sdk.messaging.debug_mode import is_debug_mode, toggle_debug_mode
from weixin_agent_sdk.messaging.send import send_message_weixin
from weixin_agent_sdk.util.logger import logger

if TYPE_CHECKING:
    from weixin_agent_sdk.api.client import WeixinApiClient


@dataclass
class SlashCommandResult:
    """斜杠命令处理结果。handled=True 表示已处理，不需要继续走 AI 管道。"""
    handled: bool


@dataclass
class SlashCommandContext:
    """斜杠命令执行所需的上下文信息。"""
    to: str
    """回复目标用户 ID。"""
    account_id: str
    """当前机器人账号 ID，用于调试模式开关。"""
    client: WeixinApiClient
    """API 客户端，用于发送回复。"""
    context_token: str | None = None
    """会话令牌，发送回复时必须携带。"""
    log: Callable[[str], None] | None = None
    """普通日志回调（可选）。"""
    err_log: Callable[[str], None] | None = None
    """错误日志回调（可选）。"""
    on_clear: Callable[[], None] | None = None
    """调用 /clear 时执行的会话清除回调（可选）。"""


async def send_reply(ctx: SlashCommandContext, text: str) -> None:
    """向用户发送回复文本，context_token 缺失时静默跳过。"""
    if not ctx.context_token:
        logger.warn(f"slash_commands: context_token 缺失，无法回复 to={ctx.to}")
        return
    await send_message_weixin(ctx.to, text, ctx.client, ctx.context_token)


async def handle_echo(
    ctx: SlashCommandContext,
    args: str,
    received_at_ms: float,
    event_timestamp_ms: float | None,
) -> None:
    """处理 /echo 命令：原样回显消息，附带通道耗时统计。"""
    message = args.strip()
    if message:
        await send_reply(ctx, message)

    event_ts = event_timestamp_ms or 0
    platform_delay = f"{int(received_at_ms - event_ts)}ms" if event_ts > 0 else "N/A"
    event_time_str = (
        datetime.fromtimestamp(event_ts / 1000).isoformat()
        if event_ts > 0
        else "N/A"
    )
    timing = "\n".join([
        "⏱ 通道耗时",
        f"├ 事件时间: {event_time_str}",
        f"├ 平台→插件: {platform_delay}",
        f"└ 插件处理: {int(time.time() * 1000 - received_at_ms)}ms",
    ])
    await send_reply(ctx, timing)


async def handle_slash_command(
    content: str,
    ctx: SlashCommandContext,
    received_at_ms: float,
    event_timestamp_ms: float | None = None,
) -> SlashCommandResult:
    """
    尝试处理斜杠命令。

    返回 handled=True 表示消息已作为命令处理，不需继续走 AI 管道；
    返回 handled=False 表示不是已知命令，调用方应继续正常流程。
    """
    trimmed = content.strip()
    if not trimmed.startswith("/"):
        return SlashCommandResult(handled=False)

    space_idx = trimmed.find(" ")
    if space_idx == -1:
        command = trimmed.lower()
        args = ""
    else:
        command = trimmed[:space_idx].lower()
        args = trimmed[space_idx + 1:]

    logger.info(f"[weixin] 斜杠命令: {command}, args: {args[:50]}")

    try:
        if command == "/echo":
            await handle_echo(ctx, args, received_at_ms, event_timestamp_ms)
            return SlashCommandResult(handled=True)

        if command == "/toggle-debug":
            enabled = toggle_debug_mode(ctx.account_id)
            await send_reply(ctx, "Debug 模式已开启" if enabled else "Debug 模式已关闭")
            return SlashCommandResult(handled=True)

        if command == "/clear":
            if ctx.on_clear is not None:
                ctx.on_clear()
            await send_reply(ctx, "✅ 会话已清除，重新开始对话")
            return SlashCommandResult(handled=True)

        # 未知命令 —— 不处理，交给 AI
        return SlashCommandResult(handled=False)

    except Exception as exc:
        logger.error(f"[weixin] 斜杠命令执行失败: {exc}")
        try:
            await send_reply(ctx, f"❌ 指令执行失败: {str(exc)[:200]}")
        except Exception:
            pass
        return SlashCommandResult(handled=True)
