"""
扫码登录：获取二维码并轮询扫码状态，直到登录成功或超时。

HTTP 调用全部委托给 WeixinApiClient（连接复用）；
本模块只负责状态机：轮询循环、二维码刷新计数、超时控制。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from weixin_agent_sdk.api.types import QrcodeStatusResp
from weixin_agent_sdk.util.logger import logger
from weixin_agent_sdk.util.redact import redact_token

if TYPE_CHECKING:
    from weixin_agent_sdk.api.client import WeixinApiClient

ACTIVE_LOGIN_TTL_S = 5 * 60          # 会话存活时间：5 分钟
MAX_QR_REFRESH_COUNT = 3             # 二维码过期最大自动刷新次数

DEFAULT_ILINK_BOT_TYPE = "3"


# ---------------------------------------------------------------------------
# 内部状态
# ---------------------------------------------------------------------------

@dataclass
class ActiveLogin:
    session_key: str
    id: str
    qrcode: str
    qrcode_url: str
    started_at: float         # time.monotonic()
    bot_token: str | None = None
    # 注：'scaned' 为服务端原始拼写（非笔误），保持不变
    status: Literal["wait", "scaned", "confirmed", "expired"] | None = None


# session_key -> ActiveLogin
active_logins = {}


def is_login_fresh(login: ActiveLogin) -> bool:
    return time.monotonic() - login.started_at < ACTIVE_LOGIN_TTL_S


def purge_expired_logins() -> None:
    expired = [k for k, v in active_logins.items() if not is_login_fresh(v)]
    for k in expired:
        del active_logins[k]


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------

@dataclass
class WeixinQrStartResult:
    """start_weixin_login_with_qr 的返回结果。"""
    session_key: str
    message: str
    qrcode: str | None = None
    qrcode_url: str | None = None


@dataclass
class WeixinQrWaitResult:
    """wait_for_weixin_login 的返回结果。"""
    connected: bool
    message: str
    bot_token: str | None = None
    account_id: str | None = None
    base_url: str | None = None
    user_id: str | None = None


def render_qr(qrcode_url: str) -> str:
    """
    将二维码 URL 渲染为终端 ASCII 字符串。
    若 qrcode 库未安装，返回原始 URL 提示行。
    """
    try:
        import io
        import qrcode as qrcode_lib
        qr = qrcode_lib.QRCode()
        qr.add_data(qrcode_url)
        qr.make(fit=True)
        buf = io.StringIO()
        qr.print_ascii(out=buf, invert=True)
        return buf.getvalue()
    except Exception:
        return f"QR Code URL: {qrcode_url}\n"


async def start_weixin_login_with_qr(
    client: WeixinApiClient,
    account_id: str | None = None,
    bot_type: str = DEFAULT_ILINK_BOT_TYPE,
    force: bool = False,
) -> WeixinQrStartResult:
    """
    发起扫码登录：向服务端申请二维码，返回会话键和二维码信息。

    若同一 session_key 已有未过期的登录会话且 force=False，直接返回缓存结果。
    """
    session_key = account_id or str(uuid.uuid4())
    purge_expired_logins()

    existing = active_logins.get(session_key)
    if not force and existing and is_login_fresh(existing) and existing.qrcode_url:
        return WeixinQrStartResult(
            session_key=session_key,
            message="二维码已就绪，请使用微信扫描。",
            qrcode=existing.qrcode,
            qrcode_url=existing.qrcode_url,
        )

    try:
        logger.info(f"Starting Weixin login with bot_type={bot_type}")
        qr_resp = await client.get_bot_qrcode(bot_type)

        qrcode = qr_resp.qrcode
        qrcode_url = qr_resp.qrcode_img_content
        logger.info(
            f"QR code received, qrcode={redact_token(qrcode)}"
            f" imgContentLen={len(qrcode_url)}"
        )
        # 二维码 URL 等同于一次性 token，不落 info 日志，仅供 debug 级别追查
        logger.debug(f"二维码链接: {redact_token(qrcode_url)}")

        active_logins[session_key] = ActiveLogin(
            session_key=session_key,
            id=str(uuid.uuid4()),
            qrcode=qrcode,
            qrcode_url=qrcode_url,
            started_at=time.monotonic(),
        )

        return WeixinQrStartResult(
            session_key=session_key,
            message="使用微信扫描以下二维码，以完成连接。",
            qrcode=qrcode,
            qrcode_url=qrcode_url,
        )
    except Exception as exc:
        logger.error(f"Failed to start Weixin login: {exc}")
        return WeixinQrStartResult(
            session_key=session_key,
            message=f"Failed to start login: {exc}",
        )


async def wait_for_weixin_login(
    session_key: str,
    client: WeixinApiClient,
    bot_type: str = DEFAULT_ILINK_BOT_TYPE,
    timeout_ms: int = 480_000,
    log: Callable[[str], None] | None = None,
) -> WeixinQrWaitResult:
    """
    轮询二维码扫码状态，直到：
      - 用户扫码确认（返回 connected=True）
      - 二维码多次过期（自动刷新，最多 MAX_QR_REFRESH_COUNT 次）
      - 超时（返回 connected=False）

    log 参数接受 (msg: str) -> None 的可调用对象，用于向调用方输出进度文字。
    """
    def noop(msg: str) -> None:
        pass

    emit = log if log is not None else noop
    active = active_logins.get(session_key)

    if not active:
        logger.warn(f"waitForWeixinLogin: no active login session_key={session_key}")
        return WeixinQrWaitResult(connected=False, message="当前没有进行中的登录，请先发起登录。")

    if not is_login_fresh(active):
        logger.warn(f"waitForWeixinLogin: login QR expired session_key={session_key}")
        del active_logins[session_key]
        return WeixinQrWaitResult(connected=False, message="二维码已过期，请重新生成。")

    timeout_s = max(timeout_ms / 1000, 1)
    deadline = time.monotonic() + timeout_s
    scanned_printed = False
    qr_refresh_count = 1

    logger.info("Starting to poll QR code status...")

    while time.monotonic() < deadline:
        try:
            status_resp: QrcodeStatusResp = await client.get_qrcode_status(active.qrcode)
            logger.debug(
                f"pollQRStatus: status={status_resp.status}"
                f" hasBotToken={bool(status_resp.bot_token)}"
                f" hasBotId={bool(status_resp.ilink_bot_id)}"
            )
            active.status = status_resp.status

            if status_resp.status == "wait":
                pass

            elif status_resp.status == "scaned":
                if not scanned_printed:
                    emit("\n已扫码，在微信继续操作...\n")
                    scanned_printed = True

            elif status_resp.status == "expired":
                qr_refresh_count += 1
                if qr_refresh_count > MAX_QR_REFRESH_COUNT:
                    logger.warn(
                        f"waitForWeixinLogin: QR expired {MAX_QR_REFRESH_COUNT} times,"
                        f" giving up session_key={session_key}"
                    )
                    del active_logins[session_key]
                    return WeixinQrWaitResult(
                        connected=False,
                        message="登录超时：二维码多次过期，请重新开始登录流程。",
                    )

                emit(f"\n二维码已过期，正在刷新...({qr_refresh_count}/{MAX_QR_REFRESH_COUNT})\n")
                logger.info(
                    f"waitForWeixinLogin: QR expired, refreshing"
                    f" ({qr_refresh_count}/{MAX_QR_REFRESH_COUNT})"
                )

                try:
                    qr_resp = await client.get_bot_qrcode(bot_type)
                    active.qrcode = qr_resp.qrcode
                    active.qrcode_url = qr_resp.qrcode_img_content
                    active.started_at = time.monotonic()
                    scanned_printed = False
                    logger.info(
                        f"waitForWeixinLogin: new QR code obtained"
                        f" qrcode={redact_token(active.qrcode)}"
                    )
                    emit("新二维码已生成，请重新扫描\n\n")
                    emit(render_qr(active.qrcode_url))
                except Exception as refresh_err:
                    logger.error(f"waitForWeixinLogin: failed to refresh QR code: {refresh_err}")
                    del active_logins[session_key]
                    return WeixinQrWaitResult(
                        connected=False,
                        message=f"刷新二维码失败: {refresh_err}",
                    )

            elif status_resp.status == "confirmed":
                if not status_resp.ilink_bot_id:
                    active_logins.pop(session_key, None)
                    logger.error("Login confirmed but ilink_bot_id missing from response")
                    return WeixinQrWaitResult(
                        connected=False,
                        message="登录失败：服务器未返回 ilink_bot_id。",
                    )

                active.bot_token = status_resp.bot_token
                active_logins.pop(session_key, None)

                logger.info(
                    f"Login confirmed! ilink_bot_id={status_resp.ilink_bot_id}"
                    f" ilink_user_id={redact_token(status_resp.ilink_user_id or '')}"
                )
                return WeixinQrWaitResult(
                    connected=True,
                    message="与微信连接成功！",
                    bot_token=status_resp.bot_token,
                    account_id=status_resp.ilink_bot_id,
                    base_url=status_resp.baseurl,
                    user_id=status_resp.ilink_user_id,
                )

        except Exception as exc:
            logger.error(f"Error polling QR status: {exc}")
            active_logins.pop(session_key, None)
            return WeixinQrWaitResult(connected=False, message=f"Login failed: {exc}")

        await asyncio.sleep(1)

    logger.warn(
        f"waitForWeixinLogin: timed out session_key={session_key} timeout_s={timeout_s}"
    )
    active_logins.pop(session_key, None)
    return WeixinQrWaitResult(connected=False, message="登录超时，请重试。")
