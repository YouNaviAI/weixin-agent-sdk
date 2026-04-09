"""
文本与媒体消息发送。

提供：
  - markdown_to_plain_text：Markdown → 微信纯文本
  - send_message_weixin：发送文本消息
  - send_image_message_weixin / send_video_message_weixin / send_file_message_weixin：发送媒体消息

所有发送函数都要求 context_token 非空；缺失时拒绝发送并抛出 ValueError。
"""

from __future__ import annotations

import base64
import re
from typing import TYPE_CHECKING

from weixin_agent_sdk.api.types import (
    CDNMedia,
    FileItem,
    ImageItem,
    MessageItem,
    MessageItemType,
    MessageState,
    MessageType,
    TextItem,
    VideoItem,
    WeixinMessage,
)
from weixin_agent_sdk.util.logger import logger
from weixin_agent_sdk.util.random_util import generate_id

if TYPE_CHECKING:
    from weixin_agent_sdk.api.client import WeixinApiClient
    from weixin_agent_sdk.cdn.upload import UploadedFileInfo


def generate_client_id() -> str:
    """生成唯一的客户端消息 ID。"""
    return generate_id("openclaw-weixin")


def markdown_to_plain_text(text: str) -> str:
    """
    将 Markdown 格式的回复转换为微信纯文本。

    处理规则：
    - 代码块：去掉围栏，保留代码内容
    - 图片：完全移除
    - 链接：保留展示文字
    - 表格：移除分隔行，列间用两空格连接
    - 粗体/斜体/删除线/行内代码：去掉标记符
    """
    result = text
    # 代码块 —— 去掉围栏，保留代码内容
    result = re.sub(r"```[^\n]*\n?([\s\S]*?)```", lambda m: m.group(1).strip(), result)
    # 图片 —— 完全移除
    result = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", result)
    # 链接 —— 保留展示文字
    result = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", result)
    # 表格分隔行 —— 移除
    result = re.sub(r"^\|[\s:|-]+\|$", "", result, flags=re.MULTILINE)
    # 表格行 —— 去掉首尾竖线，列间用两空格连接
    result = re.sub(
        r"^\|(.+)\|$",
        lambda m: "  ".join(cell.strip() for cell in m.group(1).split("|")),
        result,
        flags=re.MULTILINE,
    )
    # 内联格式标记 —— 去掉符号
    result = re.sub(r"\*\*(.+?)\*\*", r"\1", result)
    result = re.sub(r"\*(.+?)\*", r"\1", result)
    result = re.sub(r"__(.+?)__", r"\1", result)
    result = re.sub(r"_(.+?)_", r"\1", result)
    result = re.sub(r"~~(.+?)~~", r"\1", result)
    result = re.sub(r"`(.+?)`", r"\1", result)
    return result


async def send_items(
    to: str,
    context_token: str,
    items: list[MessageItem],
    client: WeixinApiClient,
    label: str,
) -> str:
    """
    逐条发送 items，每条独立请求，item_list 只含一个元素。返回最后一条的 client_id。
    """
    last_client_id = ""
    for item in items:
        client_id = generate_client_id()
        last_client_id = client_id
        msg = WeixinMessage(
            from_user_id="",
            to_user_id=to,
            client_id=client_id,
            message_type=MessageType.BOT,
            message_state=MessageState.FINISH,
            item_list=[item],
            context_token=context_token,
        )
        try:
            await client.send_message(msg)
        except Exception as exc:
            logger.error(f"{label}: 发送失败 to={to} client_id={client_id} err={exc}")
            raise
    logger.debug(f"{label}: 发送成功 to={to} client_id={last_client_id}")
    return last_client_id


async def send_message_weixin(
    to: str,
    text: str,
    client: WeixinApiClient,
    context_token: str,
) -> str:
    """
    发送纯文本消息，返回 client_id。

    context_token 是必填项；缺失时拒绝发送并抛出 ValueError。
    """
    if not context_token:
        logger.error(f"send_message_weixin: context_token 缺失，拒绝发送 to={to}")
        raise ValueError("send_message_weixin: context_token 是必填项")

    client_id = generate_client_id()
    item_list = (
        [MessageItem(type=MessageItemType.TEXT, text_item=TextItem(text=text))]
        if text
        else []
    )

    msg = WeixinMessage(
        from_user_id="",
        to_user_id=to,
        client_id=client_id,
        message_type=MessageType.BOT,
        message_state=MessageState.FINISH,
        item_list=item_list,
        context_token=context_token,
    )
    try:
        await client.send_message(msg)
    except Exception as exc:
        logger.error(f"send_message_weixin: 发送失败 to={to} client_id={client_id} err={exc}")
        raise
    return client_id


async def send_image_message_weixin(
    to: str,
    text: str,
    uploaded: UploadedFileInfo,
    client: WeixinApiClient,
    context_token: str,
) -> str:
    """
    发送图片消息（可附带文字说明），返回最后一条的 client_id。

    context_token 是必填项；缺失时拒绝发送并抛出 ValueError。
    """
    if not context_token:
        logger.error(f"send_image_message_weixin: context_token 缺失，拒绝发送 to={to}")
        raise ValueError("send_image_message_weixin: context_token 是必填项")

    logger.debug(
        f"send_image_message_weixin: to={to} filekey={uploaded.filekey}"
        f" file_size={uploaded.file_size}"
    )

    # aeskey 存储为 hex 字符串，发送时转 base64
    # TS: Buffer.from(hexString) 按 UTF-8 处理 hex 串，得到 32 个 ASCII 字节再 base64
    # Python 保持相同语义：直接编码 hex 字符串的 ASCII 字节
    aes_key_b64 = base64.b64encode(uploaded.aeskey.encode("ascii")).decode()

    image_item = MessageItem(
        type=MessageItemType.IMAGE,
        image_item=ImageItem(
            media=CDNMedia(
                encrypt_query_param=uploaded.download_encrypted_query_param,
                aes_key=aes_key_b64,
                encrypt_type=1,
            ),
            mid_size=uploaded.file_size_ciphertext,
        ),
    )

    items: list[MessageItem] = []
    if text:
        items.append(MessageItem(type=MessageItemType.TEXT, text_item=TextItem(text=text)))
    items.append(image_item)

    return await send_items(to, context_token, items, client, "send_image_message_weixin")


async def send_video_message_weixin(
    to: str,
    text: str,
    uploaded: UploadedFileInfo,
    client: WeixinApiClient,
    context_token: str,
) -> str:
    """
    发送视频消息（可附带文字说明），返回最后一条的 client_id。

    context_token 是必填项；缺失时拒绝发送并抛出 ValueError。
    """
    if not context_token:
        logger.error(f"send_video_message_weixin: context_token 缺失，拒绝发送 to={to}")
        raise ValueError("send_video_message_weixin: context_token 是必填项")

    # TS: Buffer.from(hexString) 按 UTF-8 处理 hex 串，得到 32 个 ASCII 字节再 base64
    # Python 保持相同语义：直接编码 hex 字符串的 ASCII 字节
    aes_key_b64 = base64.b64encode(uploaded.aeskey.encode("ascii")).decode()

    video_item = MessageItem(
        type=MessageItemType.VIDEO,
        video_item=VideoItem(
            media=CDNMedia(
                encrypt_query_param=uploaded.download_encrypted_query_param,
                aes_key=aes_key_b64,
                encrypt_type=1,
            ),
            video_size=uploaded.file_size_ciphertext,
        ),
    )

    items: list[MessageItem] = []
    if text:
        items.append(MessageItem(type=MessageItemType.TEXT, text_item=TextItem(text=text)))
    items.append(video_item)

    return await send_items(to, context_token, items, client, "send_video_message_weixin")


async def send_file_message_weixin(
    to: str,
    text: str,
    file_name: str,
    uploaded: UploadedFileInfo,
    client: WeixinApiClient,
    context_token: str,
) -> str:
    """
    发送文件附件消息（可附带文字说明），返回最后一条的 client_id。

    context_token 是必填项；缺失时拒绝发送并抛出 ValueError。
    """
    if not context_token:
        logger.error(f"send_file_message_weixin: context_token 缺失，拒绝发送 to={to}")
        raise ValueError("send_file_message_weixin: context_token 是必填项")

    # TS: Buffer.from(hexString) 按 UTF-8 处理 hex 串，得到 32 个 ASCII 字节再 base64
    # Python 保持相同语义：直接编码 hex 字符串的 ASCII 字节
    aes_key_b64 = base64.b64encode(uploaded.aeskey.encode("ascii")).decode()

    file_item = MessageItem(
        type=MessageItemType.FILE,
        file_item=FileItem(
            media=CDNMedia(
                encrypt_query_param=uploaded.download_encrypted_query_param,
                aes_key=aes_key_b64,
                encrypt_type=1,
            ),
            file_name=file_name,
            len=str(uploaded.file_size),
        ),
    )

    items: list[MessageItem] = []
    if text:
        items.append(MessageItem(type=MessageItemType.TEXT, text_item=TextItem(text=text)))
    items.append(file_item)

    return await send_items(to, context_token, items, client, "send_file_message_weixin")
