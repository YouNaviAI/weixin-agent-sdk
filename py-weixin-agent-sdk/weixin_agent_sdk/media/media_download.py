"""
入站媒体文件下载与解密。

从单条 MessageItem 中提取媒体信息，下载并解密到本地文件。
支持图片、语音（含 SILK→WAV 转码）、文件、视频四种类型。

对应 TS media-download.ts。
"""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import aiohttp

from weixin_agent_sdk.api.types import CDNMedia, MessageItem, MessageItemType
from weixin_agent_sdk.cdn.pic_decrypt import (
    download_and_decrypt_buffer,
    download_plain_cdn_buffer,
)
from weixin_agent_sdk.media.mime_util import get_mime_from_filename
from weixin_agent_sdk.media.silk_transcode import silk_to_wav
from weixin_agent_sdk.util.logger import logger

WEIXIN_MEDIA_MAX_BYTES = 100 * 1024 * 1024  # 单个媒体文件上限：100 MB


def sniff_image_mime(buf: bytes) -> str:
    """从图片字节头部猜测 MIME 类型，未识别时回退 image/jpeg。

    微信的图片消息没有 Content-Type 头，解密后是裸字节流，
    必须靠魔术字节判别格式以便落盘时获得正确的扩展名（.jpg/.png/...）。
    扩展名错了会导致后续 send_weixin_media_file 把图片错误地路由到
    upload_file_to_weixin（文件附件分支），微信收到的是 .bin 而非图片。

    支持 JPEG / PNG / GIF / WebP / BMP，未识别时按概率最大的 JPEG 兜底。
    """
    if len(buf) >= 3 and buf[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(buf) >= 8 and buf[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(buf) >= 6 and buf[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if len(buf) >= 12 and buf[:4] == b"RIFF" and buf[8:12] == b"WEBP":
        return "image/webp"
    if len(buf) >= 2 and buf[:2] == b"BM":
        return "image/bmp"
    return "image/jpeg"


@dataclass
class SaveMediaRequest:
    """传递给 save_media 回调的参数。"""
    buf: bytes
    content_type: str | None
    subdir: str | None
    max_bytes: int | None
    original_filename: str | None


# 保存媒体文件的回调类型：接受 SaveMediaRequest，返回本地路径
SaveMediaFn = Callable[[SaveMediaRequest], Awaitable[str]]


@dataclass
class InboundMediaResult:
    """
    单条消息媒体下载结果。

    各字段对应不同媒体类型；一条消息最多只填充其中一组。
    """
    decrypted_pic_path: str | None = None
    decrypted_voice_path: str | None = None
    voice_media_type: str | None = None      # 'audio/wav' 或 'audio/silk'
    decrypted_file_path: str | None = None
    file_media_type: str | None = None
    decrypted_video_path: str | None = None


@dataclass
class CdnPayload:
    """从 CDNMedia 中解出的下载必备字段（已校验非空）。"""
    encrypt_query_param: str
    aes_key: str


def extract_cdn_payload(media: CDNMedia | None) -> CdnPayload | None:
    """
    从 CDNMedia 提取下载必备字段。

    若 media 或必填字段缺失则返回 None；调用方据此决定是否跳过该消息。
    返回非 None 时字段已收敛为 str，避免下游再做空值检查。
    """
    if media and media.encrypt_query_param and media.aes_key:
        return CdnPayload(
            encrypt_query_param=media.encrypt_query_param,
            aes_key=media.aes_key,
        )
    return None


async def download_media_from_item(
    item: MessageItem,
    cdn_base_url: str,
    save_media: SaveMediaFn,
    label: str,
    session: aiohttp.ClientSession,
    log: Callable[[str], None] | None = None,
    err_log: Callable[[str], None] | None = None,
) -> InboundMediaResult:
    """
    从单条 MessageItem 下载并解密媒体，通过 save_media 回调持久化到本地。

    log / err_log：进度/错误输出回调，均可为 None。
    session：共享的 aiohttp.ClientSession，由调用方统一管理。

    不支持的媒体类型或媒体字段缺失时返回空 InboundMediaResult。
    下载/解密失败时记录错误日志并返回空结果，不向上抛异常（让消息循环继续运行）。
    """
    result = InboundMediaResult()

    def emit_err(msg: str) -> None:
        if err_log:
            err_log(msg)

    if item.type == MessageItemType.IMAGE:
        img = item.image_item
        if not img or not img.media or not img.media.encrypt_query_param:
            return result

        # 图片的 AES 密钥来源有两处：
        #   image_item.aeskey（hex 原始密钥，优先）或 media.aes_key（base64 编码）
        if img.aeskey:
            aes_key_base64 = base64.b64encode(bytes.fromhex(img.aeskey)).decode()
            key_source = "image_item.aeskey"
        else:
            aes_key_base64 = img.media.aes_key
            key_source = "media.aes_key"

        logger.debug(
            f"{label} image: encrypt_query_param={img.media.encrypt_query_param[:40]}..."
            f" hasAesKey={bool(aes_key_base64)} aeskeySource={key_source}"
        )
        try:
            if aes_key_base64:
                buf = await download_and_decrypt_buffer(
                    img.media.encrypt_query_param,
                    aes_key_base64,
                    cdn_base_url,
                    f"{label} image",
                    session,
                    max_bytes=WEIXIN_MEDIA_MAX_BYTES,
                )
            else:
                buf = await download_plain_cdn_buffer(
                    img.media.encrypt_query_param,
                    cdn_base_url,
                    f"{label} image-plain",
                    session,
                    max_bytes=WEIXIN_MEDIA_MAX_BYTES,
                )
            # 嗅探魔术字节得到 image/jpeg|png|gif|webp|bmp，避免落地为 .bin
            # 进而导致 send_weixin_media_file 错误路由到文件附件分支
            sniffed_mime = sniff_image_mime(buf)
            path = await save_media(SaveMediaRequest(
                buf=buf,
                content_type=sniffed_mime,
                subdir="inbound",
                max_bytes=WEIXIN_MEDIA_MAX_BYTES,
                original_filename=None,
            ))
            result.decrypted_pic_path = path
            logger.debug(f"{label} image saved: {path} sniffed_mime={sniffed_mime}")
        except Exception as exc:
            logger.error(f"{label} image download/decrypt failed: {exc}")
            emit_err(f"weixin {label} image download/decrypt failed: {exc}")

    elif item.type == MessageItemType.VOICE:
        voice = item.voice_item
        voice_payload = extract_cdn_payload(voice.media if voice else None)
        if voice_payload is None:
            return result
        try:
            silk_buf = await download_and_decrypt_buffer(
                voice_payload.encrypt_query_param,
                voice_payload.aes_key,
                cdn_base_url,
                f"{label} voice",
                session,
                max_bytes=WEIXIN_MEDIA_MAX_BYTES,
            )
            logger.debug(
                f"{label} voice: decrypted {len(silk_buf)} bytes, attempting silk transcode"
            )
            wav_buf = await silk_to_wav(silk_buf)
            if wav_buf is not None:
                path = await save_media(SaveMediaRequest(
                    buf=wav_buf,
                    content_type="audio/wav",
                    subdir="inbound",
                    max_bytes=WEIXIN_MEDIA_MAX_BYTES,
                    original_filename=None,
                ))
                result.decrypted_voice_path = path
                result.voice_media_type = "audio/wav"
                logger.debug(f"{label} voice: saved WAV to {path}")
            else:
                path = await save_media(SaveMediaRequest(
                    buf=silk_buf,
                    content_type="audio/silk",
                    subdir="inbound",
                    max_bytes=WEIXIN_MEDIA_MAX_BYTES,
                    original_filename=None,
                ))
                result.decrypted_voice_path = path
                result.voice_media_type = "audio/silk"
                logger.debug(
                    f"{label} voice: silk transcode unavailable, saved raw SILK to {path}"
                )
        except Exception as exc:
            logger.error(f"{label} voice download/transcode failed: {exc}")
            emit_err(f"weixin {label} voice download/transcode failed: {exc}")

    elif item.type == MessageItemType.FILE:
        file_item = item.file_item
        file_payload = extract_cdn_payload(file_item.media if file_item else None)
        if file_payload is None or file_item is None:
            return result
        try:
            buf = await download_and_decrypt_buffer(
                file_payload.encrypt_query_param,
                file_payload.aes_key,
                cdn_base_url,
                f"{label} file",
                session,
                max_bytes=WEIXIN_MEDIA_MAX_BYTES,
            )
            filename = file_item.file_name or "file.bin"
            mime = get_mime_from_filename(filename)
            path = await save_media(SaveMediaRequest(
                buf=buf,
                content_type=mime,
                subdir="inbound",
                max_bytes=WEIXIN_MEDIA_MAX_BYTES,
                original_filename=filename,
            ))
            result.decrypted_file_path = path
            result.file_media_type = mime
            logger.debug(f"{label} file: saved to {path} mime={mime}")
        except Exception as exc:
            logger.error(f"{label} file download failed: {exc}")
            emit_err(f"weixin {label} file download failed: {exc}")

    elif item.type == MessageItemType.VIDEO:
        video_item = item.video_item
        video_payload = extract_cdn_payload(video_item.media if video_item else None)
        if video_payload is None:
            return result
        try:
            buf = await download_and_decrypt_buffer(
                video_payload.encrypt_query_param,
                video_payload.aes_key,
                cdn_base_url,
                f"{label} video",
                session,
                max_bytes=WEIXIN_MEDIA_MAX_BYTES,
            )
            path = await save_media(SaveMediaRequest(
                buf=buf,
                content_type="video/mp4",
                subdir="inbound",
                max_bytes=WEIXIN_MEDIA_MAX_BYTES,
                original_filename=None,
            ))
            result.decrypted_video_path = path
            logger.debug(f"{label} video: saved to {path}")
        except Exception as exc:
            logger.error(f"{label} video download failed: {exc}")
            emit_err(f"weixin {label} video download failed: {exc}")

    return result
