"""
SDK 公开入口：login() 和 start()。

- login()：扫码登录，使用临时 client（async with），不与 start() 共享。
- start()：创建并拥有 SessionGuard / WeixinApiClient / cdn_session / stop_event
           四个有状态对象，注入 monitor，finally 按 LIFO 顺序关闭。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import aiohttp

from weixin_agent_sdk.api.client import WeixinApiClient
from weixin_agent_sdk.api.session_guard import SessionGuard
from weixin_agent_sdk.auth.accounts import (
    DEFAULT_BASE_URL,
    list_weixin_account_ids,
    load_config_route_tag,
    normalize_account_id,
    register_weixin_account_id,
    resolve_weixin_account,
    save_weixin_account,
    WeixinAccountData,
)
from weixin_agent_sdk.auth.login_qr import (
    DEFAULT_ILINK_BOT_TYPE,
    render_qr,
    start_weixin_login_with_qr,
    wait_for_weixin_login,
)
from weixin_agent_sdk.monitor.monitor import MonitorWeixinOpts, monitor_weixin_provider
from weixin_agent_sdk.util.logger import logger

if TYPE_CHECKING:
    from weixin_agent_sdk.agent import Agent


@dataclass
class LoginOptions:
    """login() 的可选参数。"""
    base_url: str = DEFAULT_BASE_URL
    """API 基础地址，默认使用官方地址。"""
    log: Callable[[str], None] | None = None
    """进度日志回调，默认 print。"""


@dataclass
class StartOptions:
    """start() 的可选参数。"""
    account_id: str | None = None
    """指定账号 ID，省略时自动选取第一个已登录账号。"""
    stop_event: asyncio.Event | None = None
    """设置此 Event 可优雅停止 bot；省略时运行直到任务被 cancel。"""
    log: Callable[[str], None] | None = None
    """进度日志回调，默认 print。"""


async def login(opts: LoginOptions | None = None) -> str:
    """
    扫码登录微信，打印终端二维码并等待用户扫描确认。

    成功后将凭据持久化到 ~/.openclaw/，返回 normalized account_id。
    抛出 RuntimeError 表示登录失败。

    使用临时 WeixinApiClient（async with），不持久化 client 状态。
    """
    options = opts or LoginOptions()
    log = options.log or print

    log("正在启动微信扫码登录...")

    async with WeixinApiClient(base_url=options.base_url) as client:
        start_result = await start_weixin_login_with_qr(client)

        if not start_result.qrcode_url:
            raise RuntimeError(start_result.message)

        log("\n使用微信扫描以下二维码，以完成连接：\n")
        log(render_qr(start_result.qrcode_url))
        log("\n等待扫码...\n")

        wait_result = await wait_for_weixin_login(
            session_key=start_result.session_key,
            client=client,
            bot_type=DEFAULT_ILINK_BOT_TYPE,
            timeout_ms=480_000,
            log=log,
        )

    if not wait_result.connected or not wait_result.bot_token or not wait_result.account_id:
        raise RuntimeError(wait_result.message)

    normalized_id = normalize_account_id(wait_result.account_id)
    save_weixin_account(
        normalized_id,
        WeixinAccountData(
            token=wait_result.bot_token,
            base_url=wait_result.base_url,
            user_id=wait_result.user_id,
        ),
    )
    register_weixin_account_id(normalized_id)

    log("\n✅ 与微信连接成功！")
    logger.info(f"login: 登录成功 account_id={normalized_id}")
    return normalized_id


async def start(agent: Agent, opts: StartOptions | None = None) -> None:
    """
    启动 bot 消息循环，阻塞直到 stop_event 被设置或任务被 cancel。

    生命周期所有权：
      创建 SessionGuard → WeixinApiClient → cdn_session → stop_event（若未传入），
      finally 按 LIFO 顺序关闭：cdn_session → WeixinApiClient。

    抛出：
      RuntimeError — 没有已登录账号，或账号 token 缺失。
    """
    options = opts or StartOptions()
    log = options.log or print

    # --- 解析账号 ---
    account_id = options.account_id
    if not account_id:
        ids = list_weixin_account_ids()
        if not ids:
            raise RuntimeError("没有已登录的账号，请先运行 login()")
        account_id = ids[0]
        if len(ids) > 1:
            log(f"[weixin] 检测到多个账号，使用第一个: {account_id}")

    account = resolve_weixin_account(account_id)
    if not account.configured:
        raise RuntimeError(f"账号 {account_id} 未配置 (缺少 token)，请先运行 login()")

    log(f"[weixin] 启动 bot, account={account.account_id}")
    logger.info(f"start: account={account.account_id} base_url={account.base_url}")

    # --- 创建有状态对象（LIFO 关闭顺序在 finally 中保证）---
    session_guard = SessionGuard(account.account_id)
    client = WeixinApiClient(
        base_url=account.base_url,
        token=account.token,
        route_tag_fn=lambda: load_config_route_tag(account.account_id),
        session_guard=session_guard,
    )
    cdn_session = aiohttp.ClientSession()

    # stop_event：调用方未提供时使用内部 Event（运行直到任务被 cancel）
    stop_event = options.stop_event or asyncio.Event()

    try:
        await monitor_weixin_provider(MonitorWeixinOpts(
            account_id=account.account_id,
            agent=agent,
            client=client,
            cdn_base_url=account.cdn_base_url,
            cdn_session=cdn_session,
            stop_event=stop_event,
            log=log,
        ))
    except asyncio.CancelledError:
        logger.info(f"start: task cancelled, account={account.account_id}")
        raise
    finally:
        # LIFO：cdn_session 先关，WeixinApiClient 后关
        # 嵌套 finally 确保 cdn_session 异常不会跳过 client.close()
        try:
            await cdn_session.close()
        finally:
            await client.close()
        logger.info(f"start: shutdown complete, account={account.account_id}")
