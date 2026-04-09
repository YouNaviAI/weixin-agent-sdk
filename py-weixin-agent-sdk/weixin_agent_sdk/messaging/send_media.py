"""
媒体文件上传并发送。

根据 MIME 类型路由：
  video/*  → upload_video_to_weixin  + send_video_message_weixin
  image/*  → upload_image_to_weixin  + send_image_message_weixin
  其他      → upload_file_to_weixin   + send_file_message_weixin

对应 TS send-media.ts。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp

from weixin_agent_sdk.cdn.upload import (
    upload_file_to_weixin,
    upload_image_to_weixin,
    upload_video_to_weixin,
)
from weixin_agent_sdk.media.mime_util import get_mime_from_filename
from weixin_agent_sdk.messaging.send import (
    send_file_message_weixin,
    send_image_message_weixin,
    send_video_message_weixin,
)
from weixin_agent_sdk.util.logger import logger

if TYPE_CHECKING:
    from weixin_agent_sdk.api.client import WeixinApiClient


async def send_weixin_media_file(
    file_path: str,
    to: str,
    text: str,
    client: WeixinApiClient,
    context_token: str,
    cdn_base_url: str,
    cdn_session: aiohttp.ClientSession,
) -> str:
    """
    上传本地文件并发送给微信用户，返回最后一条消息的 client_id。

    text 为可选说明文字，会作为独立 TEXT 条目先于媒体发送。
    根据文件扩展名推断 MIME 类型来决定上传接口和消息类型。
    """
    mime = get_mime_from_filename(file_path)

    if mime.startswith("video/"):
        logger.info(f"[weixin] send_weixin_media_file: 上传视频 file_path={file_path} to={to}")
        uploaded = await upload_video_to_weixin(
            file_path=file_path,
            to_user_id=to,
            client=client,
            cdn_base_url=cdn_base_url,
            cdn_session=cdn_session,
        )
        logger.info(
            f"[weixin] send_weixin_media_file: 视频上传完成"
            f" filekey={uploaded.filekey} size={uploaded.file_size}"
        )
        return await send_video_message_weixin(to, text, uploaded, client, context_token)

    if mime.startswith("image/"):
        logger.info(f"[weixin] send_weixin_media_file: 上传图片 file_path={file_path} to={to}")
        uploaded = await upload_image_to_weixin(
            file_path=file_path,
            to_user_id=to,
            client=client,
            cdn_base_url=cdn_base_url,
            cdn_session=cdn_session,
        )
        logger.info(
            f"[weixin] send_weixin_media_file: 图片上传完成"
            f" filekey={uploaded.filekey} size={uploaded.file_size}"
        )
        return await send_image_message_weixin(to, text, uploaded, client, context_token)

    # 其余类型（PDF、Word、ZIP 等）作为文件附件发送
    file_name = Path(file_path).name
    logger.info(
        f"[weixin] send_weixin_media_file: 上传文件附件"
        f" file_path={file_path} name={file_name} to={to}"
    )
    uploaded = await upload_file_to_weixin(
        file_path=file_path,
        to_user_id=to,
        client=client,
        cdn_base_url=cdn_base_url,
        cdn_session=cdn_session,
    )
    logger.info(
        f"[weixin] send_weixin_media_file: 文件上传完成"
        f" filekey={uploaded.filekey} size={uploaded.file_size}"
    )
    return await send_file_message_weixin(to, text, file_name, uploaded, client, context_token)
