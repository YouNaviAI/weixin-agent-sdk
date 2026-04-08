"""
向微信 CDN 上传 AES-128-ECB 加密文件。

重试策略：
  - CdnServerError（5xx / 网络错误 / 超时）：指数退避后重试，最多 UPLOAD_MAX_RETRIES 次。
  - CdnClientError（4xx）：立即中止，不重试。
"""

from __future__ import annotations

import asyncio

import aiohttp

from weixin_agent_sdk.cdn.pic_decrypt import CDN_UPLOAD_TIMEOUT_S, encrypt_aes_ecb
from weixin_agent_sdk.util.logger import logger
from weixin_agent_sdk.util.redact import redact_url

UPLOAD_MAX_RETRIES = 3


class CdnClientError(RuntimeError):
    """CDN 返回 4xx 客户端错误，不可重试。"""


class CdnServerError(RuntimeError):
    """CDN 返回 5xx 或网络/超时错误，可重试。"""


async def upload_buffer_to_cdn(
    buf: bytes,
    cdn_url: str,
    label: str,
    aeskey: bytes,
    session: aiohttp.ClientSession,
) -> str:
    """
    将字节数据 AES-128-ECB 加密后上传到微信 CDN。

    返回 CDN 响应头 x-encrypted-param 中的下载加密参数（downloadParam）。
    CdnClientError（4xx）立即抛出；CdnServerError / 网络 / 超时错误按指数退避重试。
    session 由调用方统一管理。
    """
    ciphertext = await asyncio.to_thread(encrypt_aes_ecb, buf, aeskey)
    timeout = aiohttp.ClientTimeout(total=CDN_UPLOAD_TIMEOUT_S)
    logger.debug(
        f"{label}: CDN POST url={redact_url(cdn_url)} ciphertextSize={len(ciphertext)}"
    )

    last_error: BaseException | None = None

    for attempt in range(1, UPLOAD_MAX_RETRIES + 1):
        try:
            async with session.post(
                cdn_url,
                data=ciphertext,
                headers={"Content-Type": "application/octet-stream"},
                timeout=timeout,
            ) as resp:
                if 400 <= resp.status < 500:
                    err_msg = resp.headers.get("x-error-message") or await resp.text()
                    logger.error(
                        f"{label}: CDN 客户端错误 attempt={attempt}"
                        f" status={resp.status} errMsg={err_msg}"
                    )
                    raise CdnClientError(
                        f"CDN upload client error {resp.status}: {err_msg}"
                    )

                if resp.status != 200:
                    err_msg = resp.headers.get("x-error-message") or f"status {resp.status}"
                    logger.error(
                        f"{label}: CDN 服务端错误 attempt={attempt}"
                        f" status={resp.status} errMsg={err_msg}"
                    )
                    raise CdnServerError(f"CDN upload server error: {err_msg}")

                download_param = resp.headers.get("x-encrypted-param")
                if not download_param:
                    logger.error(
                        f"{label}: CDN 响应缺少 x-encrypted-param 头 attempt={attempt}"
                    )
                    raise CdnServerError(
                        "CDN upload response missing x-encrypted-param header"
                    )

                logger.debug(f"{label}: CDN 上传成功 attempt={attempt}")
                return download_param

        except CdnClientError:
            raise

        except (CdnServerError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
            last_error = exc
            if attempt < UPLOAD_MAX_RETRIES:
                backoff = 0.5 * (2 ** (attempt - 1))
                logger.error(
                    f"{label}: attempt {attempt} 失败，{backoff:.1f}s 后重试... err={exc}"
                )
                await asyncio.sleep(backoff)
            else:
                logger.error(
                    f"{label}: 全部 {UPLOAD_MAX_RETRIES} 次重试均失败 err={exc}"
                )

    if last_error:
        raise last_error
    raise CdnServerError(f"CDN upload failed after {UPLOAD_MAX_RETRIES} attempts")
