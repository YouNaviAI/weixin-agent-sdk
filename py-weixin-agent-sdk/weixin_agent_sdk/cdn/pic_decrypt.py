"""
CDN 媒体文件下载与 AES-128-ECB 解密。

包含：
  - AES-128-ECB 加解密原语（对应 TS aes-ecb.ts）
  - CDN 字节下载（原始/解密两路，对应 TS pic-decrypt.ts）

AES 实现依赖 pycryptodome（Crypto.Cipher.AES）。
所有 CDN HTTP 请求均设超时，并对响应大小做流式校验防止 OOM。
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass

import aiohttp
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from weixin_agent_sdk.cdn.cdn_url import build_cdn_download_url
from weixin_agent_sdk.util.logger import logger

# CDN HTTP 超时（秒）
CDN_DOWNLOAD_TIMEOUT_S = 60
CDN_UPLOAD_TIMEOUT_S = 120

# 单次 CDN 下载的默认最大字节数（防止恶意响应 OOM）
CDN_DEFAULT_MAX_BYTES = 200 * 1024 * 1024  # 200 MB


# ---------------------------------------------------------------------------
# AES-128-ECB 原语
# ---------------------------------------------------------------------------

def encrypt_aes_ecb(plaintext: bytes, key: bytes) -> bytes:
    """使用 AES-128-ECB + PKCS7 填充加密数据。"""
    cipher = AES.new(key, AES.MODE_ECB)
    return cipher.encrypt(pad(plaintext, 16))


def decrypt_aes_ecb(ciphertext: bytes, key: bytes) -> bytes:
    """使用 AES-128-ECB + PKCS7 填充解密数据。"""
    cipher = AES.new(key, AES.MODE_ECB)
    return unpad(cipher.decrypt(ciphertext), 16)


def aes_ecb_padded_size(plaintext_size: int) -> int:
    """
    计算 AES-128-ECB + PKCS7 填充后的密文大小。

    PKCS7 至少填充 1 字节，因此明文恰好是 16 的倍数时密文会多出一个整块。
    公式等价于 TS 版：Math.ceil((plaintextSize + 1) / 16) * 16。
    """
    return ((plaintext_size + 16) // 16) * 16


# ---------------------------------------------------------------------------
# AES 密钥解析
# ---------------------------------------------------------------------------

def parse_aes_key(aes_key_base64: str, label: str) -> bytes:
    """
    将 CDNMedia.aes_key（base64）解析为原始 16 字节 AES 密钥。

    微信 CDN 存在两种编码格式：
      1. base64(原始 16 字节)     —— 图片消息
      2. base64(32 字符 hex 串)   —— 语音/文件/视频

    第二种格式下 base64 解码得到 32 个 ASCII 字符，再做 hex 解码才得到真正的 16 字节密钥。
    """
    decoded = base64.b64decode(aes_key_base64)

    if len(decoded) == 16:
        return decoded

    if len(decoded) == 32:
        try:
            hex_str = decoded.decode("ascii")
            if re.fullmatch(r"[0-9a-fA-F]{32}", hex_str):
                return bytes.fromhex(hex_str)
        except (UnicodeDecodeError, ValueError):
            pass

    raise ValueError(
        f"{label}: aes_key 必须解码为 16 字节或 32 字符 hex 串，"
        f"实际得到 {len(decoded)} 字节（base64='{aes_key_base64}'）"
    )


# ---------------------------------------------------------------------------
# CDN 字节下载（内部）
# ---------------------------------------------------------------------------

@dataclass
class FetchedBytes:
    """通用 HTTP GET 下载结果。"""
    data: bytes
    content_type: str | None  # 响应 Content-Type 头，供上层推断扩展名


async def fetch_bytes_with_limit(
    url: str,
    label: str,
    session: aiohttp.ClientSession,
    max_bytes: int = CDN_DEFAULT_MAX_BYTES,
    timeout_s: int = CDN_DOWNLOAD_TIMEOUT_S,
) -> FetchedBytes:
    """
    通用 HTTP GET 下载工具：流式读取 + 大小校验 + 超时保护。

    - session 由调用方统一管理，不在此处创建/关闭（保证连接复用）。
    - 流式读取并累计大小，超过 max_bytes 立即中止，防止 OOM。
    """
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    try:
        async with session.get(url, timeout=timeout) as resp:
            logger.debug(f"{label}: response status={resp.status} ok={resp.ok}")
            if not resp.ok:
                body = await resp.text()
                msg = f"{label}: download {resp.status} {resp.reason} body={body}"
                logger.error(msg)
                raise RuntimeError(msg)

            # 流式读取，超过 max_bytes 立即报错
            chunks = []
            total = 0
            async for chunk in resp.content.iter_chunked(65536):
                total += len(chunk)
                if total > max_bytes:
                    raise RuntimeError(
                        f"{label}: 响应超过大小限制 {max_bytes} 字节，中止下载"
                    )
                chunks.append(chunk)
            return FetchedBytes(
                data=b"".join(chunks),
                content_type=resp.headers.get("Content-Type"),
            )

    except aiohttp.ClientError as exc:
        logger.error(f"{label}: fetch network error url={url} err={exc}")
        raise


async def fetch_cdn_bytes(
    url: str,
    label: str,
    session: aiohttp.ClientSession,
    max_bytes: int = CDN_DEFAULT_MAX_BYTES,
) -> bytes:
    """从 CDN 下载原始字节，不做解密。包装 fetch_bytes_with_limit 丢弃 Content-Type。"""
    fetched = await fetch_bytes_with_limit(url, label, session, max_bytes)
    return fetched.data


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------

async def download_and_decrypt_buffer(
    encrypted_query_param: str,
    aes_key_base64: str,
    cdn_base_url: str,
    label: str,
    session: aiohttp.ClientSession,
    max_bytes: int = CDN_DEFAULT_MAX_BYTES,
) -> bytes:
    """
    从微信 CDN 下载并 AES-128-ECB 解密媒体文件，返回明文字节。

    session 由调用方管理（全局复用），aes_key_base64 支持两种 base64 格式（见 parse_aes_key）。
    """
    key = parse_aes_key(aes_key_base64, label)
    url = build_cdn_download_url(encrypted_query_param, cdn_base_url)
    logger.debug(f"{label}: fetching url={url}")
    encrypted = await fetch_cdn_bytes(url, label, session, max_bytes)
    logger.debug(f"{label}: downloaded {len(encrypted)} bytes, decrypting")
    decrypted = decrypt_aes_ecb(encrypted, key)
    logger.debug(f"{label}: decrypted {len(decrypted)} bytes")
    return decrypted


async def download_plain_cdn_buffer(
    encrypted_query_param: str,
    cdn_base_url: str,
    label: str,
    session: aiohttp.ClientSession,
    max_bytes: int = CDN_DEFAULT_MAX_BYTES,
) -> bytes:
    """从微信 CDN 下载未加密的原始字节（不做解密）。session 由调用方管理。"""
    url = build_cdn_download_url(encrypted_query_param, cdn_base_url)
    logger.debug(f"{label}: fetching url={url}")
    return await fetch_cdn_bytes(url, label, session, max_bytes)
