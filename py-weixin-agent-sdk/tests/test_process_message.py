"""
weixin_agent_sdk.messaging.process_message 的测试用例。

覆盖：
  - extract_text_body：边界/多种消息类型
  - find_media_item：直接媒体优先级、引用媒体回退、语音有转写时跳过
  - save_media_buffer：文件写入、扩展名推断
  - process_one_message：斜杠命令拦截、context_token 存储、agent 调用、
                          文本回复、媒体回复、错误通知、typing 状态
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from weixin_agent_sdk.api.types import (
    CDNMedia,
    FileItem,
    ImageItem,
    MessageItem,
    MessageItemType,
    MessageState,
    MessageType,
    TextItem,
    VideoItem,
    VoiceItem,
    WeixinMessage,
)
from weixin_agent_sdk.media.media_download import SaveMediaRequest
from weixin_agent_sdk.messaging.inbound import context_token_store
from weixin_agent_sdk.messaging.process_message import (
    MEDIA_TEMP_DIR,
    ProcessMessageDeps,
    extract_text_body,
    find_media_item,
    save_media_buffer,
)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_token_store():
    context_token_store.clear()
    yield
    context_token_store.clear()


def make_text_item(text: str) -> MessageItem:
    return MessageItem(type=MessageItemType.TEXT, text_item=TextItem(text=text))


def make_image_item(has_media: bool = True) -> MessageItem:
    media = CDNMedia(encrypt_query_param="enc=abc") if has_media else None
    return MessageItem(type=MessageItemType.IMAGE, image_item=ImageItem(media=media))


def make_video_item(has_media: bool = True) -> MessageItem:
    media = CDNMedia(encrypt_query_param="enc=vid") if has_media else None
    return MessageItem(type=MessageItemType.VIDEO, video_item=VideoItem(media=media))


def make_file_item(has_media: bool = True) -> MessageItem:
    media = CDNMedia(encrypt_query_param="enc=file") if has_media else None
    return MessageItem(type=MessageItemType.FILE, file_item=FileItem(media=media, file_name="f.txt"))


def make_voice_item(transcription: str | None = None, has_media: bool = True) -> MessageItem:
    media = CDNMedia(encrypt_query_param="enc=voice") if has_media else None
    return MessageItem(
        type=MessageItemType.VOICE,
        voice_item=VoiceItem(text=transcription, media=media),
    )


def make_message(
    item_list: list[MessageItem] | None = None,
    context_token: str = "tok_xyz",
    from_user_id: str = "user1",
) -> WeixinMessage:
    return WeixinMessage(
        from_user_id=from_user_id,
        to_user_id="bot",
        client_id="cid_test",
        message_type=MessageType.USER,
        message_state=MessageState.FINISH,
        item_list=item_list or [],
        context_token=context_token,
        create_time_ms="1700000000000",
    )


def make_deps(agent=None, typing_ticket=None) -> ProcessMessageDeps:
    client = AsyncMock()
    client.send_message = AsyncMock(return_value=None)
    client.send_typing = AsyncMock(return_value=None)
    cdn_session = AsyncMock()
    if agent is None:
        agent = AsyncMock()
        from weixin_agent_sdk.agent import ChatResponse
        agent.chat = AsyncMock(return_value=ChatResponse(text="AI 回复"))
    return ProcessMessageDeps(
        account_id="acct1",
        agent=agent,
        client=client,
        cdn_base_url="https://cdn.example.com",
        cdn_session=cdn_session,
        typing_ticket=typing_ticket,
        log=print,
        err_log=print,
    )


# ---------------------------------------------------------------------------
# extract_text_body
# ---------------------------------------------------------------------------

class TestExtractTextBody:
    def test_none_returns_empty(self):
        assert extract_text_body(None) == ""

    def test_empty_list_returns_empty(self):
        assert extract_text_body([]) == ""

    def test_text_item_extracted(self):
        assert extract_text_body([make_text_item("hello")]) == "hello"

    def test_first_text_item_used(self):
        items = [make_text_item("first"), make_text_item("second")]
        assert extract_text_body(items) == "first"

    def test_image_only_returns_empty(self):
        assert extract_text_body([make_image_item()]) == ""

    def test_image_before_text_skipped(self):
        items = [make_image_item(), make_text_item("found")]
        assert extract_text_body(items) == "found"

    def test_text_item_with_none_text_skipped(self):
        items = [MessageItem(type=MessageItemType.TEXT, text_item=TextItem(text=None))]
        assert extract_text_body(items) == ""

    def test_text_item_none_text_item_skipped(self):
        items = [MessageItem(type=MessageItemType.TEXT, text_item=None)]
        assert extract_text_body(items) == ""

    def test_voice_item_not_extracted(self):
        items = [make_voice_item("语音转文字"), make_text_item("文本")]
        # extract_text_body 只看 TEXT 类型
        assert extract_text_body(items) == "文本"


# ---------------------------------------------------------------------------
# find_media_item
# ---------------------------------------------------------------------------

class TestFindMediaItem:
    def test_none_returns_none(self):
        assert find_media_item(None) is None

    def test_empty_returns_none(self):
        assert find_media_item([]) is None

    def test_text_only_returns_none(self):
        assert find_media_item([make_text_item("hello")]) is None

    def test_image_found(self):
        item = make_image_item()
        assert find_media_item([item]) is item

    def test_video_found(self):
        item = make_video_item()
        assert find_media_item([item]) is item

    def test_file_found(self):
        item = make_file_item()
        assert find_media_item([item]) is item

    def test_voice_without_transcription_found(self):
        item = make_voice_item(transcription=None)
        assert find_media_item([item]) is item

    def test_voice_with_transcription_skipped(self):
        """有转写文字的语音应跳过（文字已包含在 body 中）。"""
        voice = make_voice_item(transcription="已转写")
        assert find_media_item([voice]) is None

    def test_image_has_priority_over_video(self):
        """IMAGE 优先级高于 VIDEO。"""
        image = make_image_item()
        video = make_video_item()
        result = find_media_item([video, image])
        assert result is image

    def test_image_without_media_skipped(self):
        """无 encrypt_query_param 的媒体 item 不可下载，应跳过。"""
        item = make_image_item(has_media=False)
        assert find_media_item([item]) is None

    def test_ref_msg_media_fallback(self):
        """无直接媒体时应回退到引用消息中的媒体。"""
        from weixin_agent_sdk.api.types import RefMessage
        ref_image = make_image_item()
        text_with_ref = MessageItem(
            type=MessageItemType.TEXT,
            text_item=TextItem(text="看这张图"),
            ref_msg=RefMessage(message_item=ref_image),
        )
        result = find_media_item([text_with_ref])
        assert result is ref_image

    def test_ref_msg_text_not_returned(self):
        """引用的是文字消息，不应作为媒体返回。"""
        from weixin_agent_sdk.api.types import RefMessage
        ref_text = make_text_item("被引用文字")
        text_with_ref = MessageItem(
            type=MessageItemType.TEXT,
            text_item=TextItem(text="回复"),
            ref_msg=RefMessage(message_item=ref_text),
        )
        assert find_media_item([text_with_ref]) is None

    def test_direct_media_before_ref_fallback(self):
        """同时有直接媒体和引用媒体时，优先返回直接媒体。"""
        from weixin_agent_sdk.api.types import RefMessage
        direct = make_image_item()
        ref_image = make_video_item()
        text_with_ref = MessageItem(
            type=MessageItemType.TEXT,
            text_item=TextItem(text="文字"),
            ref_msg=RefMessage(message_item=ref_image),
        )
        result = find_media_item([direct, text_with_ref])
        assert result is direct


# ---------------------------------------------------------------------------
# save_media_buffer
# ---------------------------------------------------------------------------

class TestSaveMediaBuffer:
    @pytest.mark.asyncio
    async def test_saves_file_and_returns_path(self, tmp_path):
        with patch(
            "weixin_agent_sdk.messaging.process_message.MEDIA_TEMP_DIR",
            str(tmp_path),
        ):
            req = SaveMediaRequest(buf=b"binary content", content_type=None, subdir="images", max_bytes=None, original_filename=None)
            path = await save_media_buffer(req)
        assert Path(path).exists()
        assert Path(path).read_bytes() == b"binary content"

    @pytest.mark.asyncio
    async def test_extension_from_original_filename(self, tmp_path):
        with patch(
            "weixin_agent_sdk.messaging.process_message.MEDIA_TEMP_DIR",
            str(tmp_path),
        ):
            req = SaveMediaRequest(buf=b"data", content_type=None, subdir=None, max_bytes=None, original_filename="photo.jpg")
            path = await save_media_buffer(req)
        assert path.endswith(".jpg")

    @pytest.mark.asyncio
    async def test_extension_from_content_type(self, tmp_path):
        with patch(
            "weixin_agent_sdk.messaging.process_message.MEDIA_TEMP_DIR",
            str(tmp_path),
        ), patch(
            "weixin_agent_sdk.messaging.process_message.get_extension_from_mime",
            return_value=".png",
        ):
            req = SaveMediaRequest(buf=b"data", content_type="image/png", subdir=None, max_bytes=None, original_filename=None)
            path = await save_media_buffer(req)
        assert path.endswith(".png")

    @pytest.mark.asyncio
    async def test_default_extension_bin(self, tmp_path):
        with patch(
            "weixin_agent_sdk.messaging.process_message.MEDIA_TEMP_DIR",
            str(tmp_path),
        ):
            req = SaveMediaRequest(buf=b"data", content_type=None, subdir=None, max_bytes=None, original_filename=None)
            path = await save_media_buffer(req)
        assert path.endswith(".bin")

    @pytest.mark.asyncio
    async def test_creates_parent_directories(self, tmp_path):
        with patch(
            "weixin_agent_sdk.messaging.process_message.MEDIA_TEMP_DIR",
            str(tmp_path),
        ):
            req = SaveMediaRequest(buf=b"data", content_type=None, subdir="a/b/c", max_bytes=None, original_filename=None)
            path = await save_media_buffer(req)
        assert Path(path).exists()

    @pytest.mark.asyncio
    async def test_empty_buf_creates_empty_file(self, tmp_path):
        with patch(
            "weixin_agent_sdk.messaging.process_message.MEDIA_TEMP_DIR",
            str(tmp_path),
        ):
            req = SaveMediaRequest(buf=b"", content_type=None, subdir=None, max_bytes=None, original_filename=None)
            path = await save_media_buffer(req)
        assert Path(path).read_bytes() == b""

    @pytest.mark.asyncio
    async def test_unique_paths_for_multiple_calls(self, tmp_path):
        with patch(
            "weixin_agent_sdk.messaging.process_message.MEDIA_TEMP_DIR",
            str(tmp_path),
        ):
            req1 = SaveMediaRequest(buf=b"data1", content_type=None, subdir=None, max_bytes=None, original_filename=None)
            req2 = SaveMediaRequest(buf=b"data2", content_type=None, subdir=None, max_bytes=None, original_filename=None)
            p1 = await save_media_buffer(req1)
            p2 = await save_media_buffer(req2)
        assert p1 != p2


# ---------------------------------------------------------------------------
# process_one_message — 斜杠命令
# ---------------------------------------------------------------------------

class TestProcessOneMessageSlash:
    @pytest.mark.asyncio
    async def test_slash_echo_stops_pipeline(self):
        """/echo 命令应被拦截，不调用 agent.chat。"""
        from weixin_agent_sdk.messaging.process_message import process_one_message
        msg = make_message([make_text_item("/echo test")])
        deps = make_deps()
        with patch(
            "weixin_agent_sdk.messaging.process_message.handle_slash_command",
            new_callable=AsyncMock,
        ) as mock_slash:
            from weixin_agent_sdk.messaging.slash_commands import SlashCommandResult
            mock_slash.return_value = SlashCommandResult(handled=True)
            await process_one_message(msg, deps)
        deps.agent.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_slash_continues_pipeline(self):
        """/unknowncmd 不被处理，应继续走 agent.chat 管道。"""
        from weixin_agent_sdk.messaging.process_message import process_one_message
        from weixin_agent_sdk.agent import ChatResponse
        msg = make_message([make_text_item("/unknowncmd")])
        deps = make_deps()
        with patch(
            "weixin_agent_sdk.messaging.process_message.handle_slash_command",
            new_callable=AsyncMock,
        ) as mock_slash:
            from weixin_agent_sdk.messaging.slash_commands import SlashCommandResult
            mock_slash.return_value = SlashCommandResult(handled=False)
            with patch(
                "weixin_agent_sdk.messaging.process_message.send_message_weixin",
                new_callable=AsyncMock,
            ):
                await process_one_message(msg, deps)
        deps.agent.chat.assert_called_once()


# ---------------------------------------------------------------------------
# process_one_message — context_token 存储
# ---------------------------------------------------------------------------

class TestProcessOneMessageContextToken:
    @pytest.mark.asyncio
    async def test_context_token_stored(self):
        from weixin_agent_sdk.messaging.process_message import process_one_message
        msg = make_message([make_text_item("hello")], context_token="tok_stored")
        deps = make_deps()
        with patch(
            "weixin_agent_sdk.messaging.process_message.send_message_weixin",
            new_callable=AsyncMock,
        ):
            await process_one_message(msg, deps)

        from weixin_agent_sdk.messaging.inbound import get_context_token
        assert get_context_token("acct1", "user1") == "tok_stored"

    @pytest.mark.asyncio
    async def test_empty_context_token_not_stored(self):
        """空 context_token 不应写入 store。"""
        from weixin_agent_sdk.messaging.process_message import process_one_message
        from weixin_agent_sdk.messaging.inbound import get_context_token
        msg = make_message([make_text_item("hello")], context_token="")
        deps = make_deps()
        with patch(
            "weixin_agent_sdk.messaging.process_message.send_message_weixin",
            new_callable=AsyncMock,
        ):
            await process_one_message(msg, deps)
        assert get_context_token("acct1", "user1") is None


# ---------------------------------------------------------------------------
# process_one_message — 正常文本回复
# ---------------------------------------------------------------------------

class TestProcessOneMessageTextReply:
    @pytest.mark.asyncio
    async def test_text_reply_sent(self):
        from weixin_agent_sdk.messaging.process_message import process_one_message
        from weixin_agent_sdk.agent import ChatResponse
        msg = make_message([make_text_item("hello")])
        agent = AsyncMock()
        agent.chat = AsyncMock(return_value=ChatResponse(text="AI says hi"))
        deps = make_deps(agent=agent)
        sent_calls = []

        async def mock_send(to, text, client, context_token):
            sent_calls.append(text)

        with patch(
            "weixin_agent_sdk.messaging.process_message.send_message_weixin",
            side_effect=mock_send,
        ):
            await process_one_message(msg, deps)

        assert any("AI says hi" in t for t in sent_calls)

    @pytest.mark.asyncio
    async def test_markdown_converted_before_send(self):
        """回复文本应经过 markdown_to_plain_text 转换。"""
        from weixin_agent_sdk.messaging.process_message import process_one_message
        from weixin_agent_sdk.agent import ChatResponse
        msg = make_message([make_text_item("hello")])
        agent = AsyncMock()
        agent.chat = AsyncMock(return_value=ChatResponse(text="**bold** reply"))
        deps = make_deps(agent=agent)
        sent_texts = []

        async def mock_send(to, text, client, context_token):
            sent_texts.append(text)

        with patch(
            "weixin_agent_sdk.messaging.process_message.send_message_weixin",
            side_effect=mock_send,
        ):
            await process_one_message(msg, deps)

        assert any("**" not in t and "bold" in t for t in sent_texts)

    @pytest.mark.asyncio
    async def test_empty_response_no_send(self):
        """Agent 返回空文本时不应调用 send_message。"""
        from weixin_agent_sdk.messaging.process_message import process_one_message
        from weixin_agent_sdk.agent import ChatResponse
        msg = make_message([make_text_item("hello")])
        agent = AsyncMock()
        agent.chat = AsyncMock(return_value=ChatResponse(text=""))
        deps = make_deps(agent=agent)

        with patch(
            "weixin_agent_sdk.messaging.process_message.send_message_weixin",
            new_callable=AsyncMock,
        ) as mock_send:
            await process_one_message(msg, deps)

        mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# process_one_message — 错误处理
# ---------------------------------------------------------------------------

class TestProcessOneMessageErrorHandling:
    @pytest.mark.asyncio
    async def test_agent_exception_sends_error_notice(self):
        """Agent 抛出异常时应发送错误通知，不重新抛出。"""
        from weixin_agent_sdk.messaging.process_message import process_one_message
        msg = make_message([make_text_item("hello")])
        agent = AsyncMock()
        agent.chat = AsyncMock(side_effect=RuntimeError("agent crashed"))
        deps = make_deps(agent=agent)

        with patch(
            "weixin_agent_sdk.messaging.process_message.send_weixin_error_notice",
            new_callable=AsyncMock,
        ) as mock_notice:
            await process_one_message(msg, deps)  # 不应抛出

        mock_notice.assert_called_once()
        # send_weixin_error_notice 以关键字参数调用，直接按名称取
        assert "agent crashed" in mock_notice.call_args.kwargs["message"]

    @pytest.mark.asyncio
    async def test_exception_does_not_propagate(self):
        """任何阶段异常都不应从 process_one_message 向外抛出。"""
        from weixin_agent_sdk.messaging.process_message import process_one_message
        msg = make_message([make_text_item("hello")])
        agent = AsyncMock()
        agent.chat = AsyncMock(side_effect=ValueError("unexpected"))
        deps = make_deps(agent=agent)

        with patch(
            "weixin_agent_sdk.messaging.process_message.send_weixin_error_notice",
            new_callable=AsyncMock,
        ):
            await process_one_message(msg, deps)  # 应正常完成，不抛出


# ---------------------------------------------------------------------------
# process_one_message — typing 状态
# ---------------------------------------------------------------------------

class TestProcessOneMessageTyping:
    @pytest.mark.asyncio
    async def test_typing_sent_when_ticket_present(self):
        """有 typing_ticket 时应发送 TYPING 状态。"""
        from weixin_agent_sdk.messaging.process_message import process_one_message, background_tasks
        msg = make_message([make_text_item("hello")])
        deps = make_deps(typing_ticket="ticket_abc")

        with patch(
            "weixin_agent_sdk.messaging.process_message.send_message_weixin",
            new_callable=AsyncMock,
        ):
            await process_one_message(msg, deps)

        deps.client.send_typing.assert_called()

    @pytest.mark.asyncio
    async def test_no_typing_when_no_ticket(self):
        """无 typing_ticket 时不应调用 send_typing。"""
        from weixin_agent_sdk.messaging.process_message import process_one_message
        msg = make_message([make_text_item("hello")])
        deps = make_deps(typing_ticket=None)

        with patch(
            "weixin_agent_sdk.messaging.process_message.send_message_weixin",
            new_callable=AsyncMock,
        ):
            await process_one_message(msg, deps)

        deps.client.send_typing.assert_not_called()


# ---------------------------------------------------------------------------
# 压力测试
# ---------------------------------------------------------------------------

class TestProcessMessageStress:
    @pytest.mark.asyncio
    async def test_many_concurrent_messages(self):
        """100 条并发消息处理应在 2s 内全部完成，不崩溃。"""
        import time
        from weixin_agent_sdk.messaging.process_message import process_one_message
        from weixin_agent_sdk.agent import ChatResponse

        async def run_one(i: int):
            msg = make_message([make_text_item(f"msg {i}")], from_user_id=f"user{i}")
            agent = AsyncMock()
            agent.chat = AsyncMock(return_value=ChatResponse(text=f"reply {i}"))
            deps = make_deps(agent=agent)
            with patch(
                "weixin_agent_sdk.messaging.process_message.send_message_weixin",
                new_callable=AsyncMock,
            ):
                await process_one_message(msg, deps)

        start = time.monotonic()
        await asyncio.gather(*[run_one(i) for i in range(100)])
        elapsed = time.monotonic() - start
        assert elapsed < 2.0, f"100 条并发消息耗时 {elapsed:.3f}s 超过 2s"
