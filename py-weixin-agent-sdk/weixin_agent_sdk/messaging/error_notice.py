"""
向用户发送纯文本错误通知。

fire-and-forget：内部错误只记录日志，绝不向调用方抛出。
context_token 缺失时静默跳过（无法关联会话，无处可发）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from weixin_agent_sdk.messaging.send import send_message_weixin
from weixin_agent_sdk.util.logger import logger

if TYPE_CHECKING:
    from weixin_agent_sdk.api.client import WeixinApiClient


async def send_weixin_error_notice(
    to: str,
    context_token: str | None,
    message: str,
    client: WeixinApiClient,
) -> None:
    """
    向用户发送错误通知文本消息。

    context_token 缺失时记录警告并跳过，不抛出异常。
    发送失败时只记录日志，不向上传播。
    """
    if not context_token:
        logger.warn(f"send_weixin_error_notice: context_token 缺失，无法通知用户 to={to}")
        return

    try:
        await send_message_weixin(to, message, client, context_token)
        logger.debug(f"send_weixin_error_notice: 已发送错误通知 to={to}")
    except Exception as exc:
        logger.error(f"send_weixin_error_notice: 发送失败 to={to}: {exc}")
