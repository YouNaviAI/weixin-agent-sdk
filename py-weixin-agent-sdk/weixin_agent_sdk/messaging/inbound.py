"""
入站消息辅助工具。

提供：
  - context_token 进程内缓存（accountId+userId → token）
  - is_media_item / body_from_item_list：消息内容提取
"""

from __future__ import annotations

from weixin_agent_sdk.api.types import MessageItem, MessageItemType
from weixin_agent_sdk.util.logger import logger

# ---------------------------------------------------------------------------
# context_token 进程内缓存
# ---------------------------------------------------------------------------

# 格式："{account_id}:{user_id}" -> context_token
# 每次 getUpdates 携带最新 context_token 后覆盖写入，
# 发送回复时从此处读取以关联会话。
context_token_store: dict[str, str] = {}


def context_token_key(account_id: str, user_id: str) -> str:
    return f"{account_id}:{user_id}"


def set_context_token(account_id: str, user_id: str, token: str) -> None:
    """存储指定账号+用户的 context_token。"""
    key = context_token_key(account_id, user_id)
    logger.debug(f"set_context_token: key={key}")
    context_token_store[key] = token


def get_context_token(account_id: str, user_id: str) -> str | None:
    """读取指定账号+用户的缓存 context_token，不存在时返回 None。"""
    key = context_token_key(account_id, user_id)
    val = context_token_store.get(key)
    logger.debug(
        f"get_context_token: key={key} found={val is not None}"
        f" store_size={len(context_token_store)}"
    )
    return val


# ---------------------------------------------------------------------------
# 消息内容工具
# ---------------------------------------------------------------------------

def is_media_item(item: MessageItem) -> bool:
    """判断消息条目是否为媒体类型（图片/视频/文件/语音）。"""
    return item.type in (
        MessageItemType.IMAGE,
        MessageItemType.VIDEO,
        MessageItemType.FILE,
        MessageItemType.VOICE,
    )


def body_from_item_list(item_list: list[MessageItem] | None) -> str:
    """
    从 item_list 提取用于传给 Agent 的消息正文。

    文本消息：直接返回文字内容；
    引用媒体消息：仅返回当前文字，被引用媒体通过 MediaPath 传递；
    引用文字消息：将被引用标题和内容拼入 [引用: ...] 前缀；
    语音转文字：直接返回 voice_item.text；
    其余情况返回空字符串。
    """
    if not item_list:
        return ""

    for item in item_list:
        if item.type == MessageItemType.TEXT and item.text_item and item.text_item.text is not None:
            text = str(item.text_item.text)
            ref = item.ref_msg
            if not ref:
                return text
            # 引用了媒体 —— 媒体通过 MediaPath 单独传递，正文只含当前文字
            if ref.message_item and is_media_item(ref.message_item):
                return text
            # 引用了文字 —— 拼入被引用内容作为上下文前缀
            parts: list[str] = []
            if ref.title:
                parts.append(ref.title)
            if ref.message_item:
                ref_body = body_from_item_list([ref.message_item])
                if ref_body:
                    parts.append(ref_body)
            if not parts:
                return text
            return f"[引用: {' | '.join(parts)}]\n{text}"

        # 语音转文字：直接使用转写内容
        if item.type == MessageItemType.VOICE and item.voice_item and item.voice_item.text:
            return item.voice_item.text

    return ""
