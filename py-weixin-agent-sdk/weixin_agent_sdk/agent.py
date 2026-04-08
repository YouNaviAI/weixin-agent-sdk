"""
Agent 接口 — 任何能处理聊天消息的 AI 后端都需要实现此接口。

实现 Agent Protocol 以将微信消息路由到自定义的 AI 服务。
微信桥接层对每条入站消息调用 `chat()`，并将返回的响应发送给用户。
"""

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable


@dataclass
class MediaAttachment:
    """从微信接收到的媒体附件（已下载并解密）。"""

    type: Literal["image", "audio", "video", "file"]
    file_path: str
    """本地文件路径（已下载并解密）。"""
    mime_type: str
    """MIME 类型，例如 'image/jpeg'、'audio/wav'。"""
    file_name: str | None = None
    """原始文件名（仅文件类附件有效）。"""


@dataclass
class ChatRequest:
    """来自微信用户的入站消息。"""

    conversation_id: str
    """会话/用户标识符，用于维护每个用户的上下文。"""
    text: str
    """消息文本内容。"""
    media: MediaAttachment | None = None
    """附带的媒体文件（如有）。"""


@dataclass
class MediaReply:
    """要发回给微信的媒体文件。"""

    type: Literal["image", "video", "file"]
    url: str
    """本地文件路径或 HTTPS URL，SDK 会自动下载远程 URL。"""
    file_name: str | None = None
    """文件名提示（用于文件类附件）。"""


@dataclass
class ChatResponse:
    """Agent 发回给微信的回复。

    `text` 和 `media` 至少需要提供其中一个。
    """

    text: str | None = None
    """回复文本（可包含 Markdown，发送前会转换为纯文本）。"""
    media: MediaReply | None = None
    """回复媒体文件（如有）。"""

    def __post_init__(self) -> None:
        if self.text is None and self.media is None:
            raise ValueError("ChatResponse 必须包含 text 或 media 中的至少一个")


@runtime_checkable
class Agent(Protocol):
    """
    任何 AI 后端都必须满足的 Protocol。

    实现 `chat()` 来处理入站微信消息并返回回复。
    如需支持 /clear 斜线命令，可选择实现 `clear_session()` 普通方法
    （非 async），桥接层会通过 hasattr() 检测其是否存在。

    注意：isinstance(obj, Agent) 仅验证 `chat` 属性是否存在，
    并不检查其是否为协程函数。建议使用鸭子类型或
    inspect.iscoroutinefunction() 替代 isinstance() 检查。
    """

    async def chat(self, request: ChatRequest) -> ChatResponse:
        """处理单条消息并返回回复。"""
        ...
