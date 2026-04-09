"""
单条消息处理流水线。

流程：
  1. 斜杠命令检测（/echo, /toggle-debug, /clear）
  2. 存储 context_token
  3. 下载并解密媒体附件
  4. 发送"正在输入"状态
  5. 调用 agent.chat()
  6. 发送回复（文本 / 媒体）
  7. 取消"正在输入"状态

对应 TS process-message.ts。
"""

from __future__ import annotations

import asyncio
import tempfile
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp

from weixin_agent_sdk.agent import ChatRequest, MediaAttachment
from weixin_agent_sdk.api.types import (
    MessageItem,
    MessageItemType,
    SendTypingReq,
    TypingStatus,
    WeixinMessage,
)
from weixin_agent_sdk.cdn.upload import download_remote_image_to_temp
from weixin_agent_sdk.media.media_download import SaveMediaRequest, download_media_from_item
from weixin_agent_sdk.media.mime_util import get_extension_from_mime
from weixin_agent_sdk.messaging.error_notice import send_weixin_error_notice
from weixin_agent_sdk.messaging.inbound import body_from_item_list, is_media_item, set_context_token
from weixin_agent_sdk.messaging.send import markdown_to_plain_text, send_message_weixin
from weixin_agent_sdk.messaging.send_media import send_weixin_media_file
from weixin_agent_sdk.messaging.slash_commands import SlashCommandContext, handle_slash_command
from weixin_agent_sdk.util.logger import logger
from weixin_agent_sdk.util.random_util import temp_file_name

if TYPE_CHECKING:
    from weixin_agent_sdk.agent import Agent
    from weixin_agent_sdk.api.client import WeixinApiClient

# 媒体临时文件目录，使用系统 temp 目录保证跨平台兼容
MEDIA_TEMP_DIR = str(Path(tempfile.gettempdir()) / "weixin-agent" / "media")

# fire-and-forget 任务集合，防止任务被 GC 提前回收
background_tasks: set[asyncio.Task] = set()


def fire_and_forget(coro) -> None:
    """
    创建后台任务，异常静默吞掉（等同 TS 的 .catch(() => {})）。
    持有任务引用防止 GC 回收。
    """
    task = asyncio.create_task(coro)
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)

    def consume_exception(t: asyncio.Task) -> None:
        if not t.cancelled():
            t.exception()  # 消费异常，避免 "Task exception was never retrieved" 警告

    task.add_done_callback(consume_exception)


async def save_media_buffer(req: SaveMediaRequest) -> str:
    """
    将媒体缓冲区写入本地临时文件，返回文件路径。

    实现 SaveMediaFn 协议：接受 SaveMediaRequest，返回 str。
    """
    subdir = req.subdir or ""
    dir_path = Path(MEDIA_TEMP_DIR) / subdir
    await asyncio.to_thread(dir_path.mkdir, parents=True, exist_ok=True)

    ext = ".bin"
    if req.original_filename:
        ext = Path(req.original_filename).suffix or ".bin"
    elif req.content_type:
        ext = get_extension_from_mime(req.content_type)

    name = temp_file_name("media", ext)
    file_path = dir_path / name
    await asyncio.to_thread(file_path.write_bytes, req.buf)
    return str(file_path)


def extract_text_body(item_list: list[MessageItem] | None) -> str:
    """提取原始文本（不含引用上下文），用于斜杠命令检测。"""
    if not item_list:
        return ""
    for item in item_list:
        if item.type == MessageItemType.TEXT and item.text_item and item.text_item.text is not None:
            return str(item.text_item.text)
    return ""


def find_media_item(item_list: list[MessageItem] | None) -> MessageItem | None:
    """
    从 item_list 中找出第一个可下载的媒体条目。

    优先级：IMAGE > VIDEO > FILE > VOICE（跳过有转写文字的语音）。
    无直接媒体时检查引用消息的媒体条目。
    """
    if not item_list:
        return None

    # 直接媒体
    direct = (
        next(
            (i for i in item_list
             if i.type == MessageItemType.IMAGE
             and i.image_item and i.image_item.media
             and i.image_item.media.encrypt_query_param),
            None,
        )
        or next(
            (i for i in item_list
             if i.type == MessageItemType.VIDEO
             and i.video_item and i.video_item.media
             and i.video_item.media.encrypt_query_param),
            None,
        )
        or next(
            (i for i in item_list
             if i.type == MessageItemType.FILE
             and i.file_item and i.file_item.media
             and i.file_item.media.encrypt_query_param),
            None,
        )
        or next(
            (i for i in item_list
             if i.type == MessageItemType.VOICE
             and i.voice_item and i.voice_item.media
             and i.voice_item.media.encrypt_query_param
             and not i.voice_item.text),
            None,
        )
    )
    if direct:
        return direct

    # 引用媒体
    for item in item_list:
        if (
            item.type == MessageItemType.TEXT
            and item.ref_msg
            and item.ref_msg.message_item
            and is_media_item(item.ref_msg.message_item)
        ):
            return item.ref_msg.message_item

    return None


@dataclass
class ProcessMessageDeps:
    """process_one_message 所需的所有外部依赖。"""
    account_id: str
    """当前账号 ID，用于 context_token 存储和调试模式。"""
    agent: Agent
    """AI 后端实例。"""
    client: WeixinApiClient
    """API 客户端（含 base_url、token、会话管理）。"""
    cdn_base_url: str
    """微信 CDN 基础地址。"""
    cdn_session: aiohttp.ClientSession
    """CDN HTTP 会话（与 API session 隔离，由调用方统一管理）。"""
    typing_ticket: str | None = None
    """getConfig 返回的打字票据，用于发送"正在输入"状态。"""
    log: Callable[[str], None] | None = None
    """普通日志回调（可选）。"""
    err_log: Callable[[str], None] | None = None
    """错误日志回调（可选）。"""


async def process_one_message(
    full: WeixinMessage,
    deps: ProcessMessageDeps,
) -> None:
    """
    处理单条入站消息的完整流水线。

    任何阶段的异常都被内部兜住，确保消息循环不中断。
    """
    received_at_ms = time.time() * 1000
    text_body = extract_text_body(full.item_list)
    to = full.from_user_id or ""

    # --- 斜杠命令检测 ---
    if text_body.startswith("/"):
        slash_ctx = SlashCommandContext(
            to=to,
            account_id=deps.account_id,
            client=deps.client,
            context_token=full.context_token,
            log=deps.log,
            err_log=deps.err_log,
            on_clear=(
                (lambda: deps.agent.clear_session(to))  # type: ignore[attr-defined]
                if hasattr(deps.agent, "clear_session")
                else None
            ),
        )
        result = await handle_slash_command(
            text_body,
            slash_ctx,
            received_at_ms,
            float(full.create_time_ms) if full.create_time_ms else None,
        )
        if result.handled:
            return

    # --- 存储 context_token ---
    context_token = full.context_token
    if context_token:
        set_context_token(deps.account_id, to, context_token)

    # --- 下载媒体 ---
    media: MediaAttachment | None = None
    media_item = find_media_item(full.item_list)
    if media_item:
        try:
            downloaded = await download_media_from_item(
                item=media_item,
                cdn_base_url=deps.cdn_base_url,
                save_media=save_media_buffer,
                label="inbound",
                session=deps.cdn_session,
                log=deps.log,
                err_log=deps.err_log,
            )
            if downloaded.decrypted_pic_path:
                media = MediaAttachment(
                    type="image",
                    file_path=downloaded.decrypted_pic_path,
                    mime_type="image/*",
                )
            elif downloaded.decrypted_video_path:
                media = MediaAttachment(
                    type="video",
                    file_path=downloaded.decrypted_video_path,
                    mime_type="video/mp4",
                )
            elif downloaded.decrypted_file_path:
                media = MediaAttachment(
                    type="file",
                    file_path=downloaded.decrypted_file_path,
                    mime_type=downloaded.file_media_type or "application/octet-stream",
                )
            elif downloaded.decrypted_voice_path:
                media = MediaAttachment(
                    type="audio",
                    file_path=downloaded.decrypted_voice_path,
                    mime_type=downloaded.voice_media_type or "audio/wav",
                )
        except Exception as exc:
            logger.error(f"process_one_message: 媒体下载失败: {exc}")

    # --- 构建 ChatRequest ---
    request = ChatRequest(
        conversation_id=to,
        text=body_from_item_list(full.item_list),
        media=media,
    )

    # --- 发送"正在输入"状态（fire-and-forget）---
    if deps.typing_ticket:
        fire_and_forget(deps.client.send_typing(SendTypingReq(
            ilink_user_id=to,
            typing_ticket=deps.typing_ticket,
            status=TypingStatus.TYPING,
        )))

    # --- 调用 Agent 并发送回复 ---
    try:
        response = await deps.agent.chat(request)

        if response.media:
            media_url = response.media.url
            if media_url.startswith("http://") or media_url.startswith("https://"):
                file_path = await download_remote_image_to_temp(
                    url=media_url,
                    dest_dir=str(Path(MEDIA_TEMP_DIR) / "outbound"),
                    session=deps.cdn_session,
                )
            else:
                file_path = (
                    media_url
                    if Path(media_url).is_absolute()
                    else str(Path(media_url).resolve())
                )
            await send_weixin_media_file(
                file_path=file_path,
                to=to,
                text=markdown_to_plain_text(response.text) if response.text else "",
                client=deps.client,
                context_token=context_token or "",
                cdn_base_url=deps.cdn_base_url,
                cdn_session=deps.cdn_session,
            )
        elif response.text:
            await send_message_weixin(
                to=to,
                text=markdown_to_plain_text(response.text),
                client=deps.client,
                context_token=context_token or "",
            )

    except Exception as exc:
        logger.error(
            f"process_one_message: agent 调用或发送失败: {exc}\n{traceback.format_exc()}"
        )
        await send_weixin_error_notice(
            to=to,
            context_token=context_token,
            message=f"⚠️ 处理消息失败：{str(exc)[:200]}",
            client=deps.client,
        )

    finally:
        # --- 取消"正在输入"状态（fire-and-forget）---
        if deps.typing_ticket:
            fire_and_forget(deps.client.send_typing(SendTypingReq(
                ilink_user_id=to,
                typing_ticket=deps.typing_ticket,
                status=TypingStatus.CANCEL,
            )))
