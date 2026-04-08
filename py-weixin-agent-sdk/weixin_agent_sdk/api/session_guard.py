"""
会话过期保护（errcode -14）。

当服务端返回 errcode -14 时，对该账号暂停所有 API 调用 1 小时，
冷却结束后自动恢复，避免被服务端限流。

使用方式：由 WeixinApiClient 持有一个 SessionGuard 实例，注入式管理。
"""

from __future__ import annotations

import datetime
import math
import time

from weixin_agent_sdk.util.logger import logger

SESSION_EXPIRED_ERRCODE = -14
PAUSE_DURATION_S = 60 * 60


class SessionPausedError(Exception):
    """会话处于冷却期内，不可发起 API 请求。"""


class SessionGuard:
    """
    单账号会话冷却管理器。

    由 WeixinApiClient 持有，在发请求前调用 assert_active()，
    收到 errcode -14 后调用 pause()。
    """

    def __init__(self, account_id: str) -> None:
        self.account_id = account_id
        self.pause_until: float | None = None  # time.monotonic() 截止时间

    def pause(self) -> None:
        """开启 1 小时冷却窗口，期间拒绝所有 API 调用。"""
        self.pause_until = time.monotonic() + PAUSE_DURATION_S
        resume_iso = format_resume_time(self.pause_until)
        logger.info(
            f"session-guard: paused account_id={self.account_id}"
            f" until={resume_iso} ({PAUSE_DURATION_S}s)"
        )

    def prune(self) -> None:
        """若冷却已自然到期则清除 pause_until。"""
        if self.pause_until is not None and time.monotonic() >= self.pause_until:
            self.pause_until = None

    def is_paused(self) -> bool:
        """检查账号是否仍处于冷却窗口内。"""
        self.prune()
        return self.pause_until is not None

    def remaining_ms(self) -> int:
        """返回冷却剩余毫秒数，未暂停时返回 0。"""
        self.prune()
        if self.pause_until is None:
            return 0
        return int((self.pause_until - time.monotonic()) * 1000)

    def assert_active(self) -> None:
        """
        断言会话处于活跃状态。

        若仍在冷却期内则抛出 SessionPausedError，
        调用方应捕获并跳过本次 API 请求。
        """
        if self.is_paused():
            remaining_min = math.ceil(self.remaining_ms() / 60_000)
            raise SessionPausedError(
                f"session paused for account_id={self.account_id},"
                f" {remaining_min} min remaining"
                f" (errcode {SESSION_EXPIRED_ERRCODE})"
            )


def format_resume_time(until_monotonic: float) -> str:
    """将 monotonic 截止时间转为易读的 UTC ISO 字符串（仅供日志使用）。"""
    delta = until_monotonic - time.monotonic()
    resume = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=delta)
    return resume.isoformat(timespec="seconds")
