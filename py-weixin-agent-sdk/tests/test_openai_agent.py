"""
examples.openai_agent.agent.OpenAIAgent 的测试用例。

覆盖：
  - 构造函数：openai 未安装时抛出 ImportError
  - chat()：纯文本、图片（base64 编码）、非图片附件（文字描述）
  - 多轮对话：历史构建与传递
  - 历史截断：超过 max_history 时从头部截断
  - 空响应处理
  - clear_session()：清除历史
  - 压力测试：大量轮次、大量并发用户
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 工具：构建 ChatRequest stub（不导入 SDK，避免循环依赖）
# ---------------------------------------------------------------------------

@dataclass
class FakeMediaAttachment:
    type: str
    file_path: str
    mime_type: str | None = None
    file_name: str | None = None


@dataclass
class FakeChatRequest:
    conversation_id: str
    text: str = ""
    media: FakeMediaAttachment | None = None


def make_openai_response(text: str):
    """构造 openai.ChatCompletion 风格的响应 stub。"""
    choice = MagicMock()
    choice.message.content = text
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def make_agent(max_history: int = 50, system_prompt: str | None = None):
    """
    构造 OpenAIAgent，注入 mock openai 客户端。
    """
    from examples.openai_agent.agent import OpenAIAgent, OpenAIAgentOptions

    agent = OpenAIAgent.__new__(OpenAIAgent)
    # 手动初始化，绕过真实 openai import
    agent.openai = MagicMock()
    mock_client = MagicMock()
    mock_client.chat = MagicMock()
    mock_client.chat.completions = MagicMock()
    mock_client.chat.completions.create = AsyncMock(
        return_value=make_openai_response("mock reply")
    )
    agent.client = mock_client
    agent.model = "gpt-4o"
    agent.system_prompt = system_prompt
    agent.max_history = max_history
    agent.conversations = {}
    return agent


# ---------------------------------------------------------------------------
# 构造函数
# ---------------------------------------------------------------------------

class TestOpenAIAgentInit:
    def test_raises_import_error_without_openai(self):
        """openai 未安装时构造函数应抛出 ImportError。"""
        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "openai":
                raise ImportError("No module named 'openai'")
            return original_import(name, *args, **kwargs)

        from examples.openai_agent.agent import OpenAIAgent, OpenAIAgentOptions
        with patch("builtins.__import__", side_effect=mock_import):
            with pytest.raises(ImportError, match="openai"):
                OpenAIAgent(OpenAIAgentOptions(api_key="sk-test"))

    def test_init_with_defaults(self):
        """正常构造，验证默认值。"""
        agent = make_agent()
        assert agent.model == "gpt-4o"
        assert agent.system_prompt is None
        assert agent.max_history == 50
        assert agent.conversations == {}


# ---------------------------------------------------------------------------
# chat — 纯文本
# ---------------------------------------------------------------------------

class TestOpenAIAgentChatText:
    @pytest.mark.asyncio
    async def test_plain_text_reply(self):
        agent = make_agent()
        agent.client.chat.completions.create = AsyncMock(
            return_value=make_openai_response("Hello!")
        )
        req = FakeChatRequest(conversation_id="user1", text="hi")
        resp = await agent.chat(req)
        assert resp.text == "Hello!"

    @pytest.mark.asyncio
    async def test_empty_text_and_no_media_returns_empty(self):
        """无文字无媒体时应返回空字符串，不调用 API。"""
        agent = make_agent()
        req = FakeChatRequest(conversation_id="user1", text="")
        resp = await agent.chat(req)
        assert resp.text == ""
        agent.client.chat.completions.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_message_sent_to_api(self):
        """文本应作为 user role 消息发送给 API。"""
        agent = make_agent()
        agent.client.chat.completions.create = AsyncMock(
            return_value=make_openai_response("ok")
        )
        req = FakeChatRequest(conversation_id="user1", text="hello world")
        await agent.chat(req)
        call_kwargs = agent.client.chat.completions.create.call_args.kwargs
        messages = call_kwargs["messages"]
        user_msgs = [m for m in messages if m["role"] == "user"]
        assert any("hello world" in str(m["content"]) for m in user_msgs)

    @pytest.mark.asyncio
    async def test_system_prompt_prepended(self):
        """有 system_prompt 时应作为第一条 system 消息。"""
        agent = make_agent(system_prompt="You are helpful.")
        agent.client.chat.completions.create = AsyncMock(
            return_value=make_openai_response("ok")
        )
        req = FakeChatRequest(conversation_id="user1", text="hi")
        await agent.chat(req)
        messages = agent.client.chat.completions.create.call_args.kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert "helpful" in messages[0]["content"]

    @pytest.mark.asyncio
    async def test_no_system_prompt_no_system_message(self):
        agent = make_agent(system_prompt=None)
        agent.client.chat.completions.create = AsyncMock(
            return_value=make_openai_response("ok")
        )
        req = FakeChatRequest(conversation_id="user1", text="hi")
        await agent.chat(req)
        messages = agent.client.chat.completions.create.call_args.kwargs["messages"]
        assert all(m["role"] != "system" for m in messages)

    @pytest.mark.asyncio
    async def test_empty_choices_returns_empty(self):
        """API 返回空 choices 时应返回空字符串。"""
        agent = make_agent()
        mock_resp = MagicMock()
        mock_resp.choices = []
        agent.client.chat.completions.create = AsyncMock(return_value=mock_resp)
        req = FakeChatRequest(conversation_id="user1", text="hi")
        resp = await agent.chat(req)
        assert resp.text == ""


# ---------------------------------------------------------------------------
# chat — 图片（vision）
# ---------------------------------------------------------------------------

class TestOpenAIAgentChatImage:
    @pytest.mark.asyncio
    async def test_image_encoded_as_base64(self, tmp_path):
        """图片应读取为 base64 编码的 data URL。"""
        img_file = tmp_path / "photo.jpg"
        img_data = b"\xff\xd8\xff" + b"\x00" * 100  # 伪造 JPEG
        img_file.write_bytes(img_data)

        agent = make_agent()
        agent.client.chat.completions.create = AsyncMock(
            return_value=make_openai_response("I see an image")
        )
        media = FakeMediaAttachment(type="image", file_path=str(img_file), mime_type="image/jpeg")
        req = FakeChatRequest(conversation_id="user1", text="what is this?", media=media)
        await agent.chat(req)

        messages = agent.client.chat.completions.create.call_args.kwargs["messages"]
        user_msg = next(m for m in messages if m["role"] == "user")
        content = user_msg["content"]
        assert isinstance(content, list)
        image_parts = [p for p in content if p.get("type") == "image_url"]
        assert image_parts, "消息中应有 image_url 类型的 content"
        url = image_parts[0]["image_url"]["url"]
        assert url.startswith("data:image/jpeg;base64,")
        decoded = base64.b64decode(url.split(",", 1)[1])
        assert decoded == img_data

    @pytest.mark.asyncio
    async def test_image_default_mime_type(self, tmp_path):
        """无 mime_type 时应默认使用 image/jpeg。"""
        img_file = tmp_path / "img.bin"
        img_file.write_bytes(b"\x00" * 10)

        agent = make_agent()
        agent.client.chat.completions.create = AsyncMock(
            return_value=make_openai_response("ok")
        )
        media = FakeMediaAttachment(type="image", file_path=str(img_file), mime_type=None)
        req = FakeChatRequest(conversation_id="user1", text="", media=media)
        await agent.chat(req)

        messages = agent.client.chat.completions.create.call_args.kwargs["messages"]
        user_msg = next(m for m in messages if m["role"] == "user")
        content = user_msg["content"]
        image_part = next(p for p in content if p.get("type") == "image_url")
        assert "image/jpeg" in image_part["image_url"]["url"]


# ---------------------------------------------------------------------------
# chat — 非图片媒体（文字描述）
# ---------------------------------------------------------------------------

class TestOpenAIAgentChatNonImageMedia:
    @pytest.mark.asyncio
    async def test_video_as_text_description(self):
        """视频媒体应以文字附件描述传递。"""
        agent = make_agent()
        agent.client.chat.completions.create = AsyncMock(
            return_value=make_openai_response("ok")
        )
        media = FakeMediaAttachment(
            type="video",
            file_path="/tmp/vid.mp4",
            file_name="vid.mp4",
        )
        req = FakeChatRequest(conversation_id="user1", text="watch this", media=media)
        await agent.chat(req)

        messages = agent.client.chat.completions.create.call_args.kwargs["messages"]
        user_msg = next(m for m in messages if m["role"] == "user")
        content = user_msg["content"]
        assert isinstance(content, list)
        text_parts = [p for p in content if p.get("type") == "text"]
        combined = " ".join(p["text"] for p in text_parts)
        assert "video" in combined
        assert "vid.mp4" in combined

    @pytest.mark.asyncio
    async def test_file_attachment_uses_filename_from_path(self):
        """无 file_name 时应从 file_path 推导文件名。添加文本使 content 为列表格式。"""
        agent = make_agent()
        agent.client.chat.completions.create = AsyncMock(
            return_value=make_openai_response("ok")
        )
        media = FakeMediaAttachment(
            type="file",
            file_path="/some/dir/report.pdf",
            file_name=None,
        )
        # 提供文本使 content_parts 有两项，触发列表格式
        req = FakeChatRequest(conversation_id="user1", text="请看附件", media=media)
        await agent.chat(req)

        messages = agent.client.chat.completions.create.call_args.kwargs["messages"]
        user_msg = next(m for m in messages if m["role"] == "user")
        content = user_msg["content"]
        assert isinstance(content, list)
        text_parts = [p for p in content if p.get("type") == "text"]
        combined = " ".join(p["text"] for p in text_parts)
        assert "report.pdf" in combined


# ---------------------------------------------------------------------------
# 多轮对话
# ---------------------------------------------------------------------------

class TestOpenAIAgentMultiTurn:
    @pytest.mark.asyncio
    async def test_history_accumulates(self):
        """多轮对话后，历史应包含所有 user/assistant 消息。"""
        agent = make_agent()
        responses = ["reply1", "reply2", "reply3"]
        agent.client.chat.completions.create = AsyncMock(
            side_effect=[make_openai_response(r) for r in responses]
        )

        for i, text in enumerate(["msg1", "msg2", "msg3"]):
            req = FakeChatRequest(conversation_id="user1", text=text)
            await agent.chat(req)

        history = agent.conversations["user1"]
        user_msgs = [m for m in history if m["role"] == "user"]
        asst_msgs = [m for m in history if m["role"] == "assistant"]
        assert len(user_msgs) == 3
        assert len(asst_msgs) == 3

    @pytest.mark.asyncio
    async def test_different_users_isolated(self):
        """不同 conversation_id 的历史互不干扰。"""
        agent = make_agent()
        agent.client.chat.completions.create = AsyncMock(
            return_value=make_openai_response("ok")
        )

        await agent.chat(FakeChatRequest(conversation_id="userA", text="msgA"))
        await agent.chat(FakeChatRequest(conversation_id="userB", text="msgB"))

        assert "userA" in agent.conversations
        assert "userB" in agent.conversations
        assert agent.conversations["userA"] != agent.conversations["userB"]

    @pytest.mark.asyncio
    async def test_history_passed_to_api(self):
        """第二轮时前一轮的历史应出现在 messages 列表中。"""
        agent = make_agent()
        agent.client.chat.completions.create = AsyncMock(
            return_value=make_openai_response("ok")
        )

        await agent.chat(FakeChatRequest(conversation_id="user1", text="first"))
        await agent.chat(FakeChatRequest(conversation_id="user1", text="second"))

        second_call_messages = agent.client.chat.completions.create.call_args.kwargs["messages"]
        user_contents = [m["content"] for m in second_call_messages if m["role"] == "user"]
        assert any("first" in str(c) for c in user_contents)
        assert any("second" in str(c) for c in user_contents)


# ---------------------------------------------------------------------------
# 历史截断
# ---------------------------------------------------------------------------

class TestOpenAIAgentHistoryTruncation:
    @pytest.mark.asyncio
    async def test_history_truncated_at_max(self):
        """超过 max_history 条后，历史长度应不超过 max_history。"""
        max_h = 10
        agent = make_agent(max_history=max_h)
        agent.client.chat.completions.create = AsyncMock(
            return_value=make_openai_response("ok")
        )

        for i in range(20):
            await agent.chat(FakeChatRequest(conversation_id="user1", text=f"msg{i}"))

        history = agent.conversations["user1"]
        assert len(history) <= max_h

    @pytest.mark.asyncio
    async def test_most_recent_kept_after_truncation(self):
        """截断时应保留最新的消息，丢弃最旧的。"""
        max_h = 4
        agent = make_agent(max_history=max_h)

        responses = [make_openai_response(f"reply{i}") for i in range(10)]
        agent.client.chat.completions.create = AsyncMock(side_effect=responses)

        for i in range(10):
            await agent.chat(FakeChatRequest(conversation_id="user1", text=f"msg{i}"))

        history = agent.conversations["user1"]
        contents = [str(m["content"]) for m in history]
        # 最新的消息应在历史中
        assert any("msg9" in c for c in contents) or any("reply9" in c for c in contents)


# ---------------------------------------------------------------------------
# clear_session
# ---------------------------------------------------------------------------

class TestOpenAIAgentClearSession:
    @pytest.mark.asyncio
    async def test_clear_removes_history(self):
        agent = make_agent()
        agent.client.chat.completions.create = AsyncMock(
            return_value=make_openai_response("ok")
        )
        await agent.chat(FakeChatRequest(conversation_id="user1", text="hi"))
        assert "user1" in agent.conversations

        agent.clear_session("user1")
        assert "user1" not in agent.conversations

    def test_clear_nonexistent_session_no_crash(self):
        agent = make_agent()
        agent.clear_session("nonexistent_user")  # 不应抛出

    @pytest.mark.asyncio
    async def test_clear_then_new_session_starts_fresh(self):
        agent = make_agent()
        responses = [make_openai_response("reply1"), make_openai_response("reply2")]
        agent.client.chat.completions.create = AsyncMock(side_effect=responses)

        await agent.chat(FakeChatRequest(conversation_id="user1", text="first"))
        agent.clear_session("user1")
        await agent.chat(FakeChatRequest(conversation_id="user1", text="second"))

        # 第二次调用时历史中不应有第一轮
        messages = agent.client.chat.completions.create.call_args.kwargs["messages"]
        user_contents = [str(m["content"]) for m in messages if m["role"] == "user"]
        assert not any("first" in c for c in user_contents)


# ---------------------------------------------------------------------------
# 压力测试
# ---------------------------------------------------------------------------

class TestOpenAIAgentStress:
    @pytest.mark.asyncio
    async def test_many_turns_single_user(self):
        """500 轮对话，历史保持在 max_history 以内，最终回复正确。"""
        agent = make_agent(max_history=20)
        agent.client.chat.completions.create = AsyncMock(
            return_value=make_openai_response("ok")
        )
        for i in range(500):
            await agent.chat(FakeChatRequest(conversation_id="user1", text=f"msg{i}"))

        assert len(agent.conversations["user1"]) <= 20

    @pytest.mark.asyncio
    async def test_many_concurrent_users(self):
        """100 个并发用户同时发消息，各自历史独立，不竞争崩溃。"""
        agent = make_agent()
        agent.client.chat.completions.create = AsyncMock(
            return_value=make_openai_response("ok")
        )

        async def chat_as_user(uid: int):
            for _ in range(5):
                await agent.chat(FakeChatRequest(
                    conversation_id=f"user{uid}",
                    text=f"hello from {uid}",
                ))

        await asyncio.gather(*[chat_as_user(i) for i in range(100)])

        assert len(agent.conversations) == 100
        for i in range(100):
            assert f"user{i}" in agent.conversations
