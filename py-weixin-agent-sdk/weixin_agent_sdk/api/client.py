"""
微信 ilink API 的异步 HTTP 客户端。

封装所有 CGI 端点（getUpdates、sendMessage、getUploadUrl、getConfig、sendTyping、
get_bot_qrcode、get_qrcode_status），使用 aiohttp 实现连接复用和精细化超时控制。
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import json
import secrets
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import aiohttp

from weixin_agent_sdk.api.session_guard import SESSION_EXPIRED_ERRCODE, SessionPausedError
from weixin_agent_sdk.api.types import (
    BotQrcodeResp,
    GetConfigResp,
    GetUpdatesResp,
    GetUploadUrlReq,
    GetUploadUrlResp,
    QrcodeStatusResp,
    SendTypingReq,
    WeixinMessage,
)
from weixin_agent_sdk.util.logger import logger
from weixin_agent_sdk.util.redact import redact_body, redact_url

if TYPE_CHECKING:
    from weixin_agent_sdk.api.session_guard import SessionGuard

# ---------------------------------------------------------------------------
# 超时常量（毫秒）
# ---------------------------------------------------------------------------

DEFAULT_LONG_POLL_TIMEOUT_MS = 35_000   # getUpdates 长轮询，服务端可持有至此
DEFAULT_API_TIMEOUT_MS = 15_000         # 常规 API（sendMessage、getUploadUrl）
DEFAULT_CONFIG_TIMEOUT_MS = 10_000      # 轻量级 API（getConfig、sendTyping）
QR_POLL_TIMEOUT_MS = 35_000            # get_qrcode_status 长轮询超时


# ---------------------------------------------------------------------------
# 包版本（写入 base_info.channel_version）
# ---------------------------------------------------------------------------

def read_channel_version() -> str:
    try:
        import importlib.metadata
        return importlib.metadata.version("weixin-agent-sdk")
    except Exception:
        return "unknown"


CHANNEL_VERSION = read_channel_version()


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------

class WeixinApiError(Exception):
    """服务端返回 HTTP 4xx/5xx 时抛出。"""

    def __init__(self, label: str, status: int, body: str) -> None:
        super().__init__(f"{label} HTTP {status}: {body}")
        self.status = status
        self.body = body


# ---------------------------------------------------------------------------
# 内部工具函数
# ---------------------------------------------------------------------------

def ensure_trailing_slash(url: str) -> str:
    return url if url.endswith("/") else url + "/"


def random_wechat_uin() -> str:
    """生成 X-WECHAT-UIN 请求头：随机 uint32 转十进制字符串再 base64 编码。"""
    uint32 = int.from_bytes(secrets.token_bytes(4), "big")
    return base64.b64encode(str(uint32).encode()).decode()


def build_base_info():
    """构建每条 API 请求都需附带的 base_info 字段。"""
    return {"channel_version": CHANNEL_VERSION}


def strip_none(d: Any) -> Any:
    """
    递归移除 dict 中值为 None 的字段，用于将 dataclass 序列化为 JSON 请求体。
    这是 dataclass → JSON 边界处理的合理例外，内部操作 dict 属于实现细节。
    """
    if isinstance(d, dict):
        return {k: strip_none(v) for k, v in d.items() if v is not None}
    if isinstance(d, list):
        return [strip_none(i) for i in d]
    return d


# ---------------------------------------------------------------------------
# WeixinApiClient
# ---------------------------------------------------------------------------

class WeixinApiClient:
    """
    微信 ilink CGI 的异步 HTTP 客户端。

    内部持有单个 aiohttp.ClientSession 以实现 TCP 连接复用。
    支持异步上下文管理器（async with），退出时自动关闭会话。

    参数:
        base_url: API 基础地址（如 https://ilinkai.weixin.qq.com）
        token: Bearer 鉴权 token
        route_tag_fn: 返回 SKRouteTag 请求头值的可调用对象（用于路由）
        session_guard: 会话过期保护器，发请求前自动调用 assert_active()，
                       收到 errcode -14 后自动调用 pause()
        long_poll_timeout_ms: getUpdates 长轮询超时（毫秒）
        api_timeout_ms: 常规 API 超时（毫秒）
        config_timeout_ms: 轻量级 API 超时（毫秒）
    """

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        route_tag_fn: Callable[[], str | None] | None = None,
        session_guard: SessionGuard | None = None,
        long_poll_timeout_ms: int = DEFAULT_LONG_POLL_TIMEOUT_MS,
        api_timeout_ms: int = DEFAULT_API_TIMEOUT_MS,
        config_timeout_ms: int = DEFAULT_CONFIG_TIMEOUT_MS,
    ) -> None:
        self.base_url = ensure_trailing_slash(base_url)
        self.token = token
        self.route_tag_fn = route_tag_fn
        self.session_guard = session_guard
        self.long_poll_timeout_ms = long_poll_timeout_ms
        self.api_timeout_ms = api_timeout_ms
        self.config_timeout_ms = config_timeout_ms
        self.http_session: aiohttp.ClientSession | None = None
        self.session_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 内部：会话管理 & 请求构建
    # ------------------------------------------------------------------

    def build_post_headers(self, body_bytes: bytes):
        headers = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Content-Length": str(len(body_bytes)),
            "X-WECHAT-UIN": random_wechat_uin(),
        }
        if self.token and self.token.strip():
            headers["Authorization"] = f"Bearer {self.token.strip()}"
        if self.route_tag_fn is not None:
            route_tag = self.route_tag_fn()
            if route_tag:
                headers["SKRouteTag"] = route_tag
        return headers

    def build_get_headers(self, extra=None):
        """构建 GET 请求头（QR 登录端点使用，无需 token）。"""
        headers = {}
        if self.route_tag_fn is not None:
            route_tag = self.route_tag_fn()
            if route_tag:
                headers["SKRouteTag"] = route_tag
        if extra:
            headers.update(extra)
        return headers

    async def get_session(self) -> aiohttp.ClientSession:
        """懒加载 aiohttp.ClientSession，保证在事件循环内创建。"""
        if self.http_session is not None and not self.http_session.closed:
            return self.http_session
        async with self.session_lock:
            # 双重检查，防止并发重建
            if self.http_session is None or self.http_session.closed:
                self.http_session = aiohttp.ClientSession()
        return self.http_session

    async def api_fetch(
        self,
        endpoint: str,
        payload: Any,
        timeout_ms: int,
        label: str,
    ) -> Any:
        """
        向指定端点发起 POST JSON 请求，返回解析后的 JSON 对象。

        - 发请求前调用 session_guard.assert_active()（若已配置）
        - 收到 errcode -14 时调用 session_guard.pause() 并抛出 SessionPausedError
        - HTTP 4xx/5xx 抛出 WeixinApiError
        """
        if self.session_guard is not None:
            self.session_guard.assert_active()

        body_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        url = self.base_url + endpoint
        headers = self.build_post_headers(body_bytes)

        logger.debug(f"POST {redact_url(url)} body={redact_body(body_bytes.decode())}")

        timeout = aiohttp.ClientTimeout(total=timeout_ms / 1000)
        session = await self.get_session()

        async with session.post(url, data=body_bytes, headers=headers, timeout=timeout) as resp:
            raw_text = await resp.text(encoding="utf-8")
            logger.debug(f"{label} status={resp.status} raw={redact_body(raw_text)}")
            if resp.status >= 400:
                raise WeixinApiError(label, resp.status, raw_text)

            parsed = json.loads(raw_text)
            if isinstance(parsed, dict):
                # -14 可能出现在 errcode 字段（getUpdates）或 ret 字段（getConfig、sendTyping 等），两个都检查
                errcode = parsed.get("errcode")
                ret = parsed.get("ret")
                if errcode == SESSION_EXPIRED_ERRCODE or ret == SESSION_EXPIRED_ERRCODE:
                    if self.session_guard is not None:
                        self.session_guard.pause()
                    raise SessionPausedError(
                        f"{label}: session expired (errcode/ret {SESSION_EXPIRED_ERRCODE}), session paused"
                    )
            return parsed

    async def api_get(
        self,
        endpoint: str,
        params: str,
        timeout_ms: int,
        label: str,
        extra_headers=None,
    ) -> str:
        """
        向指定端点发起 GET 请求，返回响应文本。

        主要用于 QR 登录端点（无需 token，不检查 errcode）。
        超时时抛出 asyncio.TimeoutError，调用方负责处理。
        """
        url = self.base_url + endpoint + ("?" + params if params else "")
        headers = self.build_get_headers(extra_headers)

        logger.debug(f"GET {redact_url(url)}")

        timeout = aiohttp.ClientTimeout(total=timeout_ms / 1000)
        session = await self.get_session()

        async with session.get(url, headers=headers, timeout=timeout) as resp:
            raw_text = await resp.text(encoding="utf-8")
            logger.debug(f"{label} status={resp.status} raw={redact_body(raw_text)}")
            if resp.status >= 400:
                raise WeixinApiError(label, resp.status, raw_text)
            return raw_text

    # ------------------------------------------------------------------
    # 公开 API 方法
    # ------------------------------------------------------------------

    async def get_updates(
        self,
        get_updates_buf: str = "",
        timeout_ms_override: int | None = None,
    ) -> GetUpdatesResp:
        """
        长轮询获取新消息。服务端会持有请求直到有新消息或超时。

        timeout_ms_override：本次请求的超时覆盖值（毫秒）。
        省略时使用构造时传入的 long_poll_timeout_ms。
        monitor 用此参数响应服务端返回的 longpolling_timeout_ms 建议。

        客户端超时为正常情况，返回 ret=0 的空响应，调用方应直接重试。
        """
        timeout_ms = timeout_ms_override if timeout_ms_override is not None else self.long_poll_timeout_ms
        try:
            parsed = await self.api_fetch(
                "ilink/bot/getupdates",
                {
                    "get_updates_buf": get_updates_buf,
                    "base_info": build_base_info(),
                },
                timeout_ms,
                "getUpdates",
            )
            return GetUpdatesResp.from_dict(parsed)
        except asyncio.TimeoutError:
            logger.debug(
                f"getUpdates: 客户端超时（{timeout_ms}ms），返回空响应"
            )
            return GetUpdatesResp(ret=0, get_updates_buf=get_updates_buf)

    async def get_upload_url(self, req: GetUploadUrlReq) -> GetUploadUrlResp:
        """获取 CDN 预签名上传 URL。"""
        payload = strip_none(dataclasses.asdict(req))
        payload["base_info"] = build_base_info()
        parsed = await self.api_fetch(
            "ilink/bot/getuploadurl", payload, self.api_timeout_ms, "getUploadUrl"
        )
        return GetUploadUrlResp.from_dict(parsed)

    async def send_message(self, msg: WeixinMessage) -> None:
        """向用户发送消息（文本或媒体）。"""
        payload = {
            "msg": strip_none(dataclasses.asdict(msg)),
            "base_info": build_base_info(),
        }
        await self.api_fetch(
            "ilink/bot/sendmessage", payload, self.api_timeout_ms, "sendMessage"
        )

    async def get_config(
        self,
        ilink_user_id: str,
        context_token: str | None = None,
    ) -> GetConfigResp:
        """获取机器人配置，响应中含 typing_ticket。"""
        payload = {
            "ilink_user_id": ilink_user_id,
            "base_info": build_base_info(),
        }
        if context_token:
            payload["context_token"] = context_token
        parsed = await self.api_fetch(
            "ilink/bot/getconfig", payload, self.config_timeout_ms, "getConfig"
        )
        return GetConfigResp.from_dict(parsed)

    async def send_typing(self, req: SendTypingReq) -> None:
        """发送正在输入状态指示器。"""
        payload = strip_none(dataclasses.asdict(req))
        payload["base_info"] = build_base_info()
        await self.api_fetch(
            "ilink/bot/sendtyping", payload, self.config_timeout_ms, "sendTyping"
        )

    async def get_bot_qrcode(self, bot_type: str) -> BotQrcodeResp:
        """申请扫码登录二维码，返回二维码标识和可扫描 URL。"""
        raw = await self.api_get(
            "ilink/bot/get_bot_qrcode",
            f"bot_type={bot_type}",
            self.api_timeout_ms,
            "getBotQrcode",
        )
        return BotQrcodeResp.from_dict(json.loads(raw))

    async def get_qrcode_status(self, qrcode: str) -> QrcodeStatusResp:
        """
        长轮询二维码状态，直到扫码结果就绪或客户端超时。

        超时时返回 status='wait'，调用方应直接重试。
        注：'scaned' 为服务端原始拼写（非笔误），保持不变。
        """
        try:
            raw = await self.api_get(
                "ilink/bot/get_qrcode_status",
                f"qrcode={qrcode}",
                QR_POLL_TIMEOUT_MS,
                "getQrcodeStatus",
                extra_headers={"iLink-App-ClientVersion": "1"},
            )
            return QrcodeStatusResp.from_dict(json.loads(raw))
        except asyncio.TimeoutError:
            logger.debug(
                f"getQrcodeStatus: 客户端超时（{QR_POLL_TIMEOUT_MS}ms），返回 wait"
            )
            return QrcodeStatusResp(status="wait")

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """关闭底层 aiohttp 会话，释放所有连接。"""
        if self.http_session is not None and not self.http_session.closed:
            await self.http_session.close()

    async def __aenter__(self) -> WeixinApiClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()
