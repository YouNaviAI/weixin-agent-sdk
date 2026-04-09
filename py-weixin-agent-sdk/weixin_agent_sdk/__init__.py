"""
weixin-agent-sdk 公开 API。

两层导出：
  顶层（普通用户）：login / start / Agent / ChatRequest / ChatResponse / MediaAttachment / MediaReply
  低层（高级用户）：WeixinApiClient / SessionGuard / MonitorWeixinOpts / ProcessMessageDeps
"""

# --- 顶层：普通用户接口 ---
from weixin_agent_sdk.agent import Agent, ChatRequest, ChatResponse, MediaAttachment, MediaReply
from weixin_agent_sdk.bot import LoginOptions, StartOptions, login, start

# --- 低层：高级编排接口 ---
from weixin_agent_sdk.api.client import WeixinApiClient, WeixinApiError
from weixin_agent_sdk.api.session_guard import SessionGuard, SessionPausedError
from weixin_agent_sdk.messaging.process_message import ProcessMessageDeps
from weixin_agent_sdk.monitor.monitor import MonitorWeixinOpts, monitor_weixin_provider

__all__ = [
    # 顶层
    "login",
    "start",
    "Agent",
    "ChatRequest",
    "ChatResponse",
    "MediaAttachment",
    "MediaReply",
    "LoginOptions",
    "StartOptions",
    # 低层
    "WeixinApiClient",
    "WeixinApiError",
    "SessionGuard",
    "SessionPausedError",
    "ProcessMessageDeps",
    "MonitorWeixinOpts",
    "monitor_weixin_provider",
]
