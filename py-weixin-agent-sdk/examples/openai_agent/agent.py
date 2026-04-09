"""
OpenAI ChatCompletions Agent 实现示例。

支持：
  - 多轮对话（per-user 消息历史）
  - 图片输入（vision，base64 编码）
  - 其他媒体以文字描述附带
  - /clear 命令重置会话（通过 clear_session() 方法）
  - 可配置模型、系统提示、自定义 base_url

使用前请安装：pip install openai
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


@dataclass
class OpenAIAgentOptions:
    """OpenAIAgent 构造参数。"""
    api_key: str
    model: str = "gpt-4o"
    base_url: str | None = None
    """自定义 API 地址（代理或兼容 API）。"""
    system_prompt: str | None = None
    """每次对话前置的系统提示，为 None 时不附加。"""
    max_history: int = 50
    """每个用户保留的最大历史消息条数（超出后从头部截断）。"""


class OpenAIAgent:
    """
    基于 OpenAI ChatCompletions API 的 Agent 实现。

    实现了 weixin_agent_sdk.Agent Protocol：
      - chat(request) -> ChatResponse
      - clear_session(conversation_id)  （可选，供 /clear 命令使用）
    """

    def __init__(self, opts: OpenAIAgentOptions) -> None:
        try:
            import openai
        except ImportError as exc:
            raise ImportError(
                "openai package 未安装，请先运行: pip install openai"
            ) from exc

        self.openai = openai
        self.client = openai.AsyncOpenAI(
            api_key=opts.api_key,
            base_url=opts.base_url,
        )
        self.model = opts.model
        self.system_prompt = opts.system_prompt
        self.max_history = opts.max_history
        # conversation_id -> 消息历史列表
        self.conversations: dict[str, list] = {}

    async def chat(self, request) -> object:
        """
        处理单条入站消息并返回 ChatResponse。

        request: weixin_agent_sdk.ChatRequest
        """
        from weixin_agent_sdk.agent import ChatResponse

        history = self.conversations.get(request.conversation_id, [])

        # 构建用户消息内容
        content_parts: list = []

        if request.text:
            content_parts.append({"type": "text", "text": request.text})

        if request.media:
            if request.media.type == "image":
                # 视觉模型：读取图片并转 base64
                image_bytes = await asyncio.to_thread(
                    Path(request.media.file_path).read_bytes
                )
                import base64
                b64 = base64.b64encode(image_bytes).decode()
                mime = request.media.mime_type or "image/jpeg"
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                })
            else:
                # 非图片媒体：以文字附件描述传递
                file_name = request.media.file_name or Path(request.media.file_path).name
                content_parts.append({
                    "type": "text",
                    "text": f"[附件: {request.media.type} — {file_name}]",
                })

        if not content_parts:
            return ChatResponse(text="")

        # 单纯文字时用字符串格式；混合内容时用列表格式
        user_content = (
            content_parts[0]["text"]
            if len(content_parts) == 1 and content_parts[0]["type"] == "text"
            else content_parts
        )
        history.append({"role": "user", "content": user_content})

        # 组装完整消息列表（可选系统提示 + 历史）
        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.extend(history)

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
        )

        reply = (response.choices[0].message.content or "") if response.choices else ""
        history.append({"role": "assistant", "content": reply})

        # 截断历史，防止无限增长
        if len(history) > self.max_history:
            del history[: len(history) - self.max_history]

        self.conversations[request.conversation_id] = history
        return ChatResponse(text=reply)

    def clear_session(self, conversation_id: str) -> None:
        """清除指定用户的对话历史，供 /clear 命令使用。"""
        self.conversations.pop(conversation_id, None)
