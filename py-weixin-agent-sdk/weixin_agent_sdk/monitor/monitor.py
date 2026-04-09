"""
微信长轮询主循环（getUpdates orchestrator）。

职责：
  - 管理 get_updates_buf 断点续传
  - 三段式错误处理：session_expired / api_error / transport_error
  - 3 次连续失败后退避 30s，所有 sleep 可被 stop_event 中断
  - 响应服务端建议的 longpolling_timeout_ms（每轮动态调整）
  - 每条消息以独立 asyncio.Task 运行，避免慢 Agent 阻塞后续消息
  - 退出前最多等待 10s gather 未完成的消息任务

对应 TS monitor/monitor.ts。
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import aiohttp

from weixin_agent_sdk.api.config_cache import WeixinConfigManager
from weixin_agent_sdk.api.session_guard import SessionPausedError
from weixin_agent_sdk.messaging.process_message import ProcessMessageDeps, process_one_message
from weixin_agent_sdk.storage.sync_buf import (
    get_sync_buf_file_path,
    load_get_updates_buf,
    save_get_updates_buf,
)
from weixin_agent_sdk.util.logger import logger

if TYPE_CHECKING:
    from weixin_agent_sdk.agent import Agent
    from weixin_agent_sdk.api.client import WeixinApiClient

DEFAULT_LONG_POLL_TIMEOUT_MS = 35_000
MAX_CONSECUTIVE_FAILURES = 3
BACKOFF_DELAY_MS = 30_000
RETRY_DELAY_MS = 2_000
STOP_GATHER_TIMEOUT_S = 10


@dataclass
class MonitorWeixinOpts:
    """monitor_weixin_provider 的启动参数。"""
    account_id: str
    agent: Agent
    client: WeixinApiClient
    """API 客户端，已绑定 base_url、token、session_guard。"""
    cdn_base_url: str
    cdn_session: aiohttp.ClientSession
    """CDN HTTP 会话，与 API session 隔离，由调用方（bot.py）管理生命周期。"""
    stop_event: asyncio.Event
    """设置此 Event 可中断循环，退出前最多等待 STOP_GATHER_TIMEOUT_S 秒。"""
    log: Callable[[str], None] | None = None


async def interruptible_sleep(seconds: float, stop_event: asyncio.Event) -> None:
    """
    等待指定秒数，但 stop_event 被设置时立即返回。

    对应 TS 的 sleep(ms, abortSignal)。
    """
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass


async def monitor_weixin_provider(opts: MonitorWeixinOpts) -> None:
    """
    长轮询主循环：getUpdates → 分发消息 → 等待下次轮询。

    运行直到 stop_event 被设置。
    退出前最多等待 STOP_GATHER_TIMEOUT_S 秒让 in-flight 消息任务完成。
    """
    account_id = opts.account_id
    client = opts.client
    stop_event = opts.stop_event
    log = opts.log or (lambda msg: print(msg))

    def err_log(msg: str) -> None:
        log(msg)
        logger.error(msg)

    a_log = logger.with_account(account_id)

    log(f"[weixin] monitor started (account={account_id})")
    a_log.info(f"Monitor started: cdn_base_url={opts.cdn_base_url}")

    # 断点续传 buf
    sync_file_path = get_sync_buf_file_path(account_id)
    previous_buf = load_get_updates_buf(sync_file_path)
    get_updates_buf = previous_buf.get_updates_buf if previous_buf else ""

    if previous_buf:
        log(f"[weixin] resuming from previous sync buf ({len(get_updates_buf)} bytes)")
    else:
        log("[weixin] no previous sync buf, starting fresh")

    # WeixinConfigManager 是 monitor 独占的纯内存缓存，绑定账号 logger 方便多账号排查
    config_manager = WeixinConfigManager(client=client, log=a_log)

    # 服务端建议的长轮询超时，每轮从响应中更新
    next_timeout_ms: int | None = None

    consecutive_failures = 0

    # 每条消息的独立处理任务集合（退出前 gather）
    message_tasks: set[asyncio.Task] = set()

    def on_message_task_done(task: asyncio.Task) -> None:
        message_tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            a_log.error(f"process_one_message task 意外失败: {task.exception()}")

    try:
        while not stop_event.is_set():
            try:
                resp = await client.get_updates(
                    get_updates_buf,
                    timeout_ms_override=next_timeout_ms,
                )

            except SessionPausedError:
                # 会话已冷却（errcode -14），client 内部已调用 session_guard.pause()
                remaining_s = (
                    client.session_guard.remaining_ms() / 1000
                    if client.session_guard
                    else 3600
                )
                err_log(
                    f"[weixin] session expired (errcode -14), pausing for"
                    f" {math.ceil(remaining_s / 60)} min"
                )
                consecutive_failures = 0
                await interruptible_sleep(remaining_s, stop_event)
                continue

            except Exception as exc:
                if stop_event.is_set():
                    break
                consecutive_failures += 1
                err_log(
                    f"[weixin] getUpdates transport error"
                    f" ({consecutive_failures}/{MAX_CONSECUTIVE_FAILURES}): {exc}"
                )
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    err_log(
                        f"[weixin] {MAX_CONSECUTIVE_FAILURES} consecutive failures,"
                        " backing off 30s"
                    )
                    consecutive_failures = 0
                    await interruptible_sleep(BACKOFF_DELAY_MS / 1000, stop_event)
                else:
                    await interruptible_sleep(RETRY_DELAY_MS / 1000, stop_event)
                continue

            # 响应服务端建议的长轮询超时
            if resp.longpolling_timeout_ms and resp.longpolling_timeout_ms > 0:
                next_timeout_ms = resp.longpolling_timeout_ms

            # --- API 层错误检测（非 transport 层）---
            is_api_error = (
                (resp.ret is not None and resp.ret != 0)
                or (resp.errcode is not None and resp.errcode != 0)
            )

            if is_api_error:
                consecutive_failures += 1
                err_log(
                    f"[weixin] getUpdates api error: ret={resp.ret} errcode={resp.errcode}"
                    f" errmsg={resp.errmsg or ''}"
                    f" ({consecutive_failures}/{MAX_CONSECUTIVE_FAILURES})"
                )
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    err_log(
                        f"[weixin] {MAX_CONSECUTIVE_FAILURES} consecutive failures,"
                        " backing off 30s"
                    )
                    consecutive_failures = 0
                    await interruptible_sleep(BACKOFF_DELAY_MS / 1000, stop_event)
                else:
                    await interruptible_sleep(RETRY_DELAY_MS / 1000, stop_event)
                continue

            consecutive_failures = 0

            # --- 持久化断点续传 buf ---
            if resp.get_updates_buf:
                save_get_updates_buf(sync_file_path, resp.get_updates_buf)
                get_updates_buf = resp.get_updates_buf

            # --- 分发每条消息（独立 Task，fire-and-forget）---
            for full in resp.msgs:
                from_uid = full.from_user_id or ""
                item_types = ",".join(
                    str(i.type) for i in (full.item_list or [])
                ) or "none"
                a_log.info(f"inbound: from={from_uid} types={item_types}")

                cached_config = await config_manager.get_for_user(from_uid, full.context_token)

                deps = ProcessMessageDeps(
                    account_id=account_id,
                    agent=opts.agent,
                    client=client,
                    cdn_base_url=opts.cdn_base_url,
                    cdn_session=opts.cdn_session,
                    typing_ticket=cached_config.typing_ticket,
                    log=log,
                    err_log=err_log,
                )

                task = asyncio.create_task(process_one_message(full, deps))
                message_tasks.add(task)
                task.add_done_callback(on_message_task_done)

    finally:
        # 退出前等待 in-flight 消息任务，最多 STOP_GATHER_TIMEOUT_S 秒
        if message_tasks:
            a_log.info(
                f"Monitor stopping, waiting for {len(message_tasks)} message task(s)..."
            )
            try:
                await asyncio.wait_for(
                    asyncio.gather(*message_tasks, return_exceptions=True),
                    timeout=STOP_GATHER_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                a_log.warn(
                    f"Monitor stop: {len(message_tasks)} task(s) still running after"
                    f" {STOP_GATHER_TIMEOUT_S}s, letting them detach"
                )

    a_log.info("Monitor ended")
