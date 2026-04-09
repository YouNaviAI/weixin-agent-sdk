"""
带 TTL 缓存和指数退避重试的 getConfig 管理器。

每个用户最多每 24 小时刷新一次配置，失败时以指数退避重试（最长 1 小时）。
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from weixin_agent_sdk.util.logger import Logger

if TYPE_CHECKING:
    from weixin_agent_sdk.api.client import WeixinApiClient

CONFIG_CACHE_TTL_MS = 24 * 60 * 60 * 1000       # 成功后的随机刷新窗口（24 小时）
INITIAL_RETRY_MS = 2_000                          # 首次失败后等待 2 秒
MAX_RETRY_MS = 60 * 60 * 1000                     # 最长退避 1 小时


@dataclass
class CachedConfig:
    """getConfig 中我们实际使用的字段子集。"""
    typing_ticket: str


@dataclass
class CacheEntry:
    config: CachedConfig
    ever_succeeded: bool
    next_fetch_at_monotonic: float   # 下次允许拉取的 time.monotonic() 时间点
    retry_delay_ms: float            # 当前退避间隔


def now_monotonic_ms() -> float:
    return time.monotonic() * 1000


class WeixinConfigManager:
    """
    带 TTL 缓存和指数退避重试的 getConfig 缓存管理器。

    依赖 WeixinApiClient 拉取配置，每个用户独立维护一个缓存条目。
    """

    def __init__(self, client: WeixinApiClient, log: Logger | None = None) -> None:
        self.client = client
        self.log = log or Logger()
        self.cache = {}

    async def get_for_user(
        self,
        user_id: str,
        context_token: str | None = None,
    ) -> CachedConfig:
        """
        获取指定用户的缓存配置。

        若缓存不存在或已过期，立即拉取一次 getConfig；
        失败时使用指数退避，并返回上次缓存（若有）。
        """
        now = now_monotonic_ms()
        entry = self.cache.get(user_id)
        should_fetch = entry is None or now >= entry.next_fetch_at_monotonic

        if should_fetch:
            fetch_ok = False
            try:
                resp = await self.client.get_config(user_id, context_token)
                if resp.ret is not None and resp.ret == 0:
                    self.cache[user_id] = CacheEntry(
                        config=CachedConfig(typing_ticket=resp.typing_ticket or ""),
                        ever_succeeded=True,
                        # 成功后在 24 小时内随机选一个时间点刷新，避免集中请求
                        next_fetch_at_monotonic=now + random.random() * CONFIG_CACHE_TTL_MS,
                        retry_delay_ms=INITIAL_RETRY_MS,
                    )
                    action = "refreshed" if (entry and entry.ever_succeeded) else "cached"
                    self.log.info(f"[weixin] config {action} for {user_id}")
                    fetch_ok = True
            except Exception as exc:
                self.log.warn(f"[weixin] getConfig failed for {user_id} (ignored): {exc}")

            if not fetch_ok:
                prev_delay = entry.retry_delay_ms if entry else INITIAL_RETRY_MS
                next_delay = min(prev_delay * 2, MAX_RETRY_MS)
                if entry:
                    entry.next_fetch_at_monotonic = now + next_delay
                    entry.retry_delay_ms = next_delay
                else:
                    self.cache[user_id] = CacheEntry(
                        config=CachedConfig(typing_ticket=""),
                        ever_succeeded=False,
                        next_fetch_at_monotonic=now + INITIAL_RETRY_MS,
                        retry_delay_ms=INITIAL_RETRY_MS,
                    )

        cached = self.cache.get(user_id)
        return cached.config if cached else CachedConfig(typing_ticket="")
