"""
媒体上传总入口。

提供三个公开函数（上传图片/视频/文件附件），共享同一条流水线：
  读取文件 → 计算 MD5 → 生成 AES 密钥 → getUploadUrl → uploadBufferToCdn → 返回结果

对应 TS upload.ts。
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp

from weixin_agent_sdk.api.types import GetUploadUrlReq, UploadMediaType
from weixin_agent_sdk.cdn.cdn_url import build_cdn_upload_url
from weixin_agent_sdk.cdn.cdn_upload import upload_buffer_to_cdn
from weixin_agent_sdk.cdn.pic_decrypt import (
    CDN_DEFAULT_MAX_BYTES,
    aes_ecb_padded_size,
    fetch_bytes_with_limit,
)
from weixin_agent_sdk.media.mime_util import get_extension_from_content_type_or_url
from weixin_agent_sdk.util.logger import logger
from weixin_agent_sdk.util.random_util import temp_file_name

if TYPE_CHECKING:
    from weixin_agent_sdk.api.client import WeixinApiClient


@dataclass
class UploadedFileInfo:
    """上传完成后返回的文件元数据。"""
    filekey: str
    # CDN 返回的下载加密参数，填入 ImageItem.media.encrypt_query_param
    download_encrypted_query_param: str
    # AES-128-ECB 密钥，hex 编码；转为 base64 后填入 CDNMedia.aes_key
    aeskey: str
    file_size: int            # 明文大小（字节）
    file_size_ciphertext: int # AES-128-ECB 加密后大小（含 PKCS7 填充）


# ---------------------------------------------------------------------------
# 工具：从远端 URL 下载到本地临时文件
# ---------------------------------------------------------------------------

async def download_remote_image_to_temp(
    url: str,
    dest_dir: str,
    session: aiohttp.ClientSession,
    max_bytes: int = CDN_DEFAULT_MAX_BYTES,
) -> str:
    """
    下载远端图片/媒体到本地临时文件，返回本地路径。
    扩展名从 Content-Type 或 URL 推断。

    session 由调用方统一管理。下载走 fetch_bytes_with_limit，自带超时和大小校验。
    """
    logger.debug(f"download_remote_image_to_temp: fetching url={url}")
    fetched = await fetch_bytes_with_limit(
        url, "download_remote_image_to_temp", session, max_bytes
    )

    logger.debug(f"download_remote_image_to_temp: downloaded {len(fetched.data)} bytes")
    await asyncio.to_thread(Path(dest_dir).mkdir, parents=True, exist_ok=True)
    ext = get_extension_from_content_type_or_url(fetched.content_type, url)
    name = temp_file_name("weixin-remote", ext)
    file_path = str(Path(dest_dir) / name)
    await asyncio.to_thread(Path(file_path).write_bytes, fetched.data)
    logger.debug(f"download_remote_image_to_temp: saved to {file_path} ext={ext}")
    return file_path


# ---------------------------------------------------------------------------
# 核心上传流水线（内部）
# ---------------------------------------------------------------------------

async def upload_media_to_cdn(
    file_path: str,
    to_user_id: str,
    client: WeixinApiClient,
    cdn_base_url: str,
    media_type: int,
    label: str,
    cdn_session: aiohttp.ClientSession,
) -> UploadedFileInfo:
    """
    通用上传流水线：读取本地文件 → 计算哈希 → 申请上传 URL → 加密上传到 CDN。
    cdn_session 由调用方统一管理，与 API client session 分离。
    """
    plaintext = await asyncio.to_thread(Path(file_path).read_bytes)
    rawsize = len(plaintext)
    rawfilemd5 = hashlib.md5(plaintext).hexdigest()
    filesize = aes_ecb_padded_size(rawsize)
    filekey = secrets.token_hex(16)
    aeskey_bytes = secrets.token_bytes(16)

    logger.debug(
        f"{label}: file={file_path} rawsize={rawsize} filesize={filesize}"
        f" md5={rawfilemd5} filekey={filekey}"
    )

    upload_url_resp = await client.get_upload_url(
        GetUploadUrlReq(
            filekey=filekey,
            media_type=media_type,
            to_user_id=to_user_id,
            rawsize=rawsize,
            rawfilemd5=rawfilemd5,
            filesize=filesize,
            no_need_thumb=True,
            aeskey=aeskey_bytes.hex(),
        )
    )

    # 优先使用新格式 upload_full_url，回退到旧格式 upload_param + 自行拼接
    if upload_url_resp.upload_full_url:
        cdn_url = upload_url_resp.upload_full_url
    elif upload_url_resp.upload_param:
        cdn_url = build_cdn_upload_url(cdn_base_url, upload_url_resp.upload_param, filekey)
    else:
        msg = (
            f"{label}: getUploadUrl 既未返回 upload_full_url 也未返回 upload_param，"
            f"resp={upload_url_resp}"
        )
        logger.error(msg)
        raise RuntimeError(msg)

    download_encrypted_query_param = await upload_buffer_to_cdn(
        buf=plaintext,
        cdn_url=cdn_url,
        label=f"{label}[orig filekey={filekey}]",
        aeskey=aeskey_bytes,
        session=cdn_session,
    )

    return UploadedFileInfo(
        filekey=filekey,
        download_encrypted_query_param=download_encrypted_query_param,
        aeskey=aeskey_bytes.hex(),
        file_size=rawsize,
        file_size_ciphertext=filesize,
    )


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------

async def upload_file_to_weixin(
    file_path: str,
    to_user_id: str,
    client: WeixinApiClient,
    cdn_base_url: str,
    cdn_session: aiohttp.ClientSession,
) -> UploadedFileInfo:
    """上传本地图片文件到微信 CDN（media_type=IMAGE）。"""
    return await upload_media_to_cdn(
        file_path=file_path,
        to_user_id=to_user_id,
        client=client,
        cdn_base_url=cdn_base_url,
        media_type=UploadMediaType.IMAGE,
        label="uploadFileToWeixin",
        cdn_session=cdn_session,
    )


async def upload_video_to_weixin(
    file_path: str,
    to_user_id: str,
    client: WeixinApiClient,
    cdn_base_url: str,
    cdn_session: aiohttp.ClientSession,
) -> UploadedFileInfo:
    """上传本地视频文件到微信 CDN（media_type=VIDEO）。"""
    return await upload_media_to_cdn(
        file_path=file_path,
        to_user_id=to_user_id,
        client=client,
        cdn_base_url=cdn_base_url,
        media_type=UploadMediaType.VIDEO,
        label="uploadVideoToWeixin",
        cdn_session=cdn_session,
    )


async def upload_file_attachment_to_weixin(
    file_path: str,
    to_user_id: str,
    client: WeixinApiClient,
    cdn_base_url: str,
    cdn_session: aiohttp.ClientSession,
) -> UploadedFileInfo:
    """上传本地文件附件到微信 CDN（media_type=FILE，无需缩略图）。"""
    return await upload_media_to_cdn(
        file_path=file_path,
        to_user_id=to_user_id,
        client=client,
        cdn_base_url=cdn_base_url,
        media_type=UploadMediaType.FILE,
        label="uploadFileAttachmentToWeixin",
        cdn_session=cdn_session,
    )
