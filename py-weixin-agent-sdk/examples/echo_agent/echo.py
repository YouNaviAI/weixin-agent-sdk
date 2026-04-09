"""
最小回显 Agent —— 用于端到端验证 SDK 完整链路。

行为：
  - 文本消息 → 回显 "你说: <文本>"
  - 图片/视频/文件 → 原样回显，附带文字说明（验证出站媒体上传链路）
  - 语音 → 因 MediaReply 不支持 audio 类型，回退为文字摘要
  - 引用消息 → SDK 已把引用上下文拼到 request.text 里，正常回显即可
"""

from __future__ import annotations

from pathlib import Path

from weixin_agent_sdk import ChatRequest, ChatResponse, MediaReply

# 入站媒体类型 → 中文标签，仅用于回显时的人类可读说明
MEDIA_TYPE_LABEL = {
    "image": "图片",
    "video": "视频",
    "file": "文件",
}


class EchoAgent:
    """无状态回显 Agent，原样返回收到的内容。

    实现 weixin_agent_sdk.Agent Protocol（duck-typed，无需显式继承）。
    无状态意味着不需要 clear_session 方法，/clear 命令仍能工作。
    """

    async def chat(self, request: ChatRequest) -> ChatResponse:
        """处理单条消息，返回回显回复。"""
        media = request.media

        # --- 媒体消息分支 ---
        if media is not None:
            try:
                file_size = Path(media.file_path).stat().st_size
            except OSError as exc:
                # 媒体文件读取失败时退化为纯文字诊断
                return ChatResponse(
                    text=f"❌ 媒体文件读取失败: {exc}\n路径: {media.file_path}"
                )

            # 语音不能回显为语音（MediaReply 类型不支持 audio），回退文字
            if media.type == "audio":
                return ChatResponse(
                    text=(
                        f"[收到语音] 大小: {file_size} 字节\n"
                        f"MIME: {media.mime_type}\n"
                        f"路径: {media.file_path}"
                    )
                )

            # image / video / file 直接回显
            type_label = MEDIA_TYPE_LABEL.get(media.type, media.type)
            caption = f"收到{type_label} ({file_size} 字节)，原样回显"
            if request.text:
                caption = f"{caption}\n附带文字: {request.text}"

            return ChatResponse(
                text=caption,
                media=MediaReply(
                    type=media.type,
                    url=media.file_path,
                    file_name=media.file_name,
                ),
            )

        # --- 纯文本分支 ---
        if request.text:
            return ChatResponse(text=f"你说: {request.text}")

        # --- 兜底（理论不可达，微信不允许发空消息）---
        return ChatResponse(text="（收到空消息）")
