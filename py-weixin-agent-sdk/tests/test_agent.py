"""weixin_agent_sdk.agent — 接口定义与验证的测试用例。"""

import pytest

from weixin_agent_sdk.agent import (
    Agent,
    ChatRequest,
    ChatResponse,
    MediaAttachment,
    MediaReply,
)


# ---------------------------------------------------------------------------
# MediaAttachment
# ---------------------------------------------------------------------------

class TestMediaAttachment:
    def test_required_fields(self):
        m = MediaAttachment(type="image", file_path="/tmp/a.jpg", mime_type="image/jpeg")
        assert m.type == "image"
        assert m.file_path == "/tmp/a.jpg"
        assert m.mime_type == "image/jpeg"
        assert m.file_name is None

    def test_optional_file_name(self):
        m = MediaAttachment(type="file", file_path="/tmp/doc.pdf", mime_type="application/pdf", file_name="doc.pdf")
        assert m.file_name == "doc.pdf"

    def test_all_valid_types(self):
        for t in ("image", "audio", "video", "file"):
            m = MediaAttachment(type=t, file_path="/tmp/x", mime_type="application/octet-stream")
            assert m.type == t


# ---------------------------------------------------------------------------
# ChatRequest
# ---------------------------------------------------------------------------

class TestChatRequest:
    def test_text_only(self):
        req = ChatRequest(conversation_id="user1", text="hello")
        assert req.conversation_id == "user1"
        assert req.text == "hello"
        assert req.media is None

    def test_with_media(self):
        media = MediaAttachment(type="image", file_path="/tmp/img.png", mime_type="image/png")
        req = ChatRequest(conversation_id="user1", text="", media=media)
        assert req.media is media

    def test_empty_text_is_valid(self):
        req = ChatRequest(conversation_id="user1", text="")
        assert req.text == ""


# ---------------------------------------------------------------------------
# ChatResponse
# ---------------------------------------------------------------------------

class TestChatResponse:
    def test_text_only(self):
        r = ChatResponse(text="hello")
        assert r.text == "hello"
        assert r.media is None

    def test_media_only(self):
        media = MediaReply(type="image", url="/tmp/img.png")
        r = ChatResponse(media=media)
        assert r.media is media
        assert r.text is None

    def test_text_and_media(self):
        media = MediaReply(type="file", url="/tmp/doc.pdf", file_name="doc.pdf")
        r = ChatResponse(text="here is the file", media=media)
        assert r.text == "here is the file"
        assert r.media is media

    def test_both_none_raises(self):
        with pytest.raises(ValueError, match="ChatResponse"):
            ChatResponse()

    def test_both_none_explicit_raises(self):
        with pytest.raises(ValueError):
            ChatResponse(text=None, media=None)


# ---------------------------------------------------------------------------
# MediaReply
# ---------------------------------------------------------------------------

class TestMediaReply:
    def test_required_fields(self):
        m = MediaReply(type="image", url="/tmp/img.png")
        assert m.type == "image"
        assert m.url == "/tmp/img.png"
        assert m.file_name is None

    def test_all_valid_types(self):
        for t in ("image", "video", "file"):
            m = MediaReply(type=t, url="/tmp/x")
            assert m.type == t

    def test_https_url(self):
        m = MediaReply(type="image", url="https://example.com/img.png")
        assert m.url.startswith("https://")


# ---------------------------------------------------------------------------
# Agent Protocol
# ---------------------------------------------------------------------------

class TestAgentProtocol:
    def test_valid_async_impl_passes_isinstance(self):
        class MyAgent:
            async def chat(self, request: ChatRequest) -> ChatResponse:
                return ChatResponse(text="ok")

        assert isinstance(MyAgent(), Agent)

    def test_missing_chat_fails_isinstance(self):
        class BadAgent:
            pass

        assert not isinstance(BadAgent(), Agent)

    def test_sync_chat_passes_isinstance(self):
        # runtime_checkable 只检查属性是否存在，不验证是否为 async。
        # 此测试用于记录该已知限制。
        class SyncAgent:
            def chat(self, request: ChatRequest) -> ChatResponse:
                return ChatResponse(text="ok")

        assert isinstance(SyncAgent(), Agent)  # 通过 — Protocol 的已知局限
