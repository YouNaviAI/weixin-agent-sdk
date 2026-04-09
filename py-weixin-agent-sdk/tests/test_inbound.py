"""
weixin_agent_sdk.messaging.inbound 的测试用例。

覆盖：
  - body_from_item_list：文本提取、引用处理、语音转文字
  - is_media_item：所有消息类型判断
  - context_token_store：set / get / 多账号隔离
"""

import asyncio

import pytest

from weixin_agent_sdk.api.types import (
    CDNMedia,
    FileItem,
    ImageItem,
    MessageItem,
    MessageItemType,
    RefMessage,
    TextItem,
    VideoItem,
    VoiceItem,
)
from weixin_agent_sdk.messaging.inbound import (
    body_from_item_list,
    context_token_store,
    get_context_token,
    is_media_item,
    set_context_token,
)


# ---------------------------------------------------------------------------
# Fixture：每个测试前清空 context_token_store
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_token_store():
    context_token_store.clear()
    yield
    context_token_store.clear()


# ---------------------------------------------------------------------------
# is_media_item
# ---------------------------------------------------------------------------

class TestIsMediaItem:
    def test_image_is_media(self):
        assert is_media_item(MessageItem(type=MessageItemType.IMAGE))

    def test_video_is_media(self):
        assert is_media_item(MessageItem(type=MessageItemType.VIDEO))

    def test_file_is_media(self):
        assert is_media_item(MessageItem(type=MessageItemType.FILE))

    def test_voice_is_media(self):
        assert is_media_item(MessageItem(type=MessageItemType.VOICE))

    def test_text_is_not_media(self):
        assert not is_media_item(MessageItem(type=MessageItemType.TEXT))

    def test_none_type_is_not_media(self):
        assert not is_media_item(MessageItem(type=MessageItemType.NONE))

    def test_unknown_type_is_not_media(self):
        assert not is_media_item(MessageItem(type=99))


# ---------------------------------------------------------------------------
# body_from_item_list — 边界条件
# ---------------------------------------------------------------------------

class TestBodyFromItemListBoundary:
    def test_none_returns_empty(self):
        assert body_from_item_list(None) == ""

    def test_empty_list_returns_empty(self):
        assert body_from_item_list([]) == ""

    def test_image_only_returns_empty(self):
        assert body_from_item_list([MessageItem(type=MessageItemType.IMAGE)]) == ""

    def test_voice_without_text_returns_empty(self):
        item = MessageItem(
            type=MessageItemType.VOICE,
            voice_item=VoiceItem(text=None),
        )
        assert body_from_item_list([item]) == ""

    def test_text_item_with_none_text_returns_empty(self):
        item = MessageItem(type=MessageItemType.TEXT, text_item=TextItem(text=None))
        assert body_from_item_list([item]) == ""

    def test_text_item_without_text_item_returns_empty(self):
        item = MessageItem(type=MessageItemType.TEXT, text_item=None)
        assert body_from_item_list([item]) == ""

    def test_only_first_text_item_used(self):
        items = [
            MessageItem(type=MessageItemType.TEXT, text_item=TextItem(text="first")),
            MessageItem(type=MessageItemType.TEXT, text_item=TextItem(text="second")),
        ]
        assert body_from_item_list(items) == "first"

    def test_non_text_item_before_text_item_skipped(self):
        items = [
            MessageItem(type=MessageItemType.IMAGE),
            MessageItem(type=MessageItemType.TEXT, text_item=TextItem(text="hello")),
        ]
        assert body_from_item_list(items) == "hello"


# ---------------------------------------------------------------------------
# body_from_item_list — 正常文本
# ---------------------------------------------------------------------------

class TestBodyFromItemListText:
    def test_simple_text(self):
        item = MessageItem(type=MessageItemType.TEXT, text_item=TextItem(text="你好"))
        assert body_from_item_list([item]) == "你好"

    def test_empty_string_text(self):
        item = MessageItem(type=MessageItemType.TEXT, text_item=TextItem(text=""))
        assert body_from_item_list([item]) == ""

    def test_text_with_newlines(self):
        text = "line1\nline2\nline3"
        item = MessageItem(type=MessageItemType.TEXT, text_item=TextItem(text=text))
        assert body_from_item_list([item]) == text

    def test_unicode_and_emoji(self):
        text = "🎉 Hello 中文 こんにちは"
        item = MessageItem(type=MessageItemType.TEXT, text_item=TextItem(text=text))
        assert body_from_item_list([item]) == text

    def test_no_ref_msg_returns_plain(self):
        item = MessageItem(
            type=MessageItemType.TEXT,
            text_item=TextItem(text="reply"),
            ref_msg=None,
        )
        assert body_from_item_list([item]) == "reply"


# ---------------------------------------------------------------------------
# body_from_item_list — 引用消息
# ---------------------------------------------------------------------------

class TestBodyFromItemListQuoted:
    def test_quoted_media_returns_current_text_only(self):
        """引用了媒体时，只返回当前文字，不附加引用前缀。"""
        ref_item = MessageItem(type=MessageItemType.IMAGE)
        item = MessageItem(
            type=MessageItemType.TEXT,
            text_item=TextItem(text="看这张图"),
            ref_msg=RefMessage(message_item=ref_item, title="图片"),
        )
        assert body_from_item_list([item]) == "看这张图"

    def test_quoted_text_with_title_and_body(self):
        ref_text = MessageItem(type=MessageItemType.TEXT, text_item=TextItem(text="原文内容"))
        item = MessageItem(
            type=MessageItemType.TEXT,
            text_item=TextItem(text="我的回复"),
            ref_msg=RefMessage(message_item=ref_text, title="原文标题"),
        )
        result = body_from_item_list([item])
        assert result.startswith("[引用: 原文标题 | 原文内容]")
        assert "我的回复" in result

    def test_quoted_text_title_only(self):
        item = MessageItem(
            type=MessageItemType.TEXT,
            text_item=TextItem(text="回复"),
            ref_msg=RefMessage(message_item=None, title="被引用标题"),
        )
        result = body_from_item_list([item])
        assert "[引用: 被引用标题]" in result
        assert "回复" in result

    def test_quoted_text_no_parts_returns_plain(self):
        """引用消息既无 title 也无 message_item 时，仅返回当前文字。"""
        item = MessageItem(
            type=MessageItemType.TEXT,
            text_item=TextItem(text="回复"),
            ref_msg=RefMessage(message_item=None, title=None),
        )
        assert body_from_item_list([item]) == "回复"

    def test_quoted_voice_item_treated_as_media(self):
        """引用了语音消息，语音是媒体类型，只返回当前文字。"""
        ref_voice = MessageItem(type=MessageItemType.VOICE)
        item = MessageItem(
            type=MessageItemType.TEXT,
            text_item=TextItem(text="回复语音"),
            ref_msg=RefMessage(message_item=ref_voice, title=None),
        )
        assert body_from_item_list([item]) == "回复语音"


# ---------------------------------------------------------------------------
# body_from_item_list — 语音转文字
# ---------------------------------------------------------------------------

class TestBodyFromItemListVoice:
    def test_voice_with_transcription(self):
        item = MessageItem(
            type=MessageItemType.VOICE,
            voice_item=VoiceItem(text="这是语音转文字内容"),
        )
        assert body_from_item_list([item]) == "这是语音转文字内容"

    def test_voice_transcription_before_text_item(self):
        """语音在文本项之前：优先返回语音转文字。"""
        items = [
            MessageItem(type=MessageItemType.VOICE, voice_item=VoiceItem(text="语音")),
            MessageItem(type=MessageItemType.TEXT, text_item=TextItem(text="文本")),
        ]
        assert body_from_item_list(items) == "语音"


# ---------------------------------------------------------------------------
# body_from_item_list — 压力测试
# ---------------------------------------------------------------------------

class TestBodyFromItemListStress:
    def test_large_item_list(self):
        """1000 个非文本条目后跟一个文本条目，应能在 < 100ms 内返回。"""
        import time
        items = [MessageItem(type=MessageItemType.IMAGE) for _ in range(1000)]
        items.append(MessageItem(type=MessageItemType.TEXT, text_item=TextItem(text="found")))

        start = time.monotonic()
        result = body_from_item_list(items)
        elapsed = time.monotonic() - start

        assert result == "found"
        assert elapsed < 0.1, f"遍历耗时 {elapsed:.3f}s，超过 100ms"

    def test_very_long_text(self):
        """100KB 文本内容，应原样返回。"""
        long_text = "A" * 100_000
        item = MessageItem(type=MessageItemType.TEXT, text_item=TextItem(text=long_text))
        assert body_from_item_list([item]) == long_text

    def test_deeply_nested_quote(self):
        """三层引用嵌套，不应栈溢出。"""
        inner = MessageItem(type=MessageItemType.TEXT, text_item=TextItem(text="inner"))
        mid = MessageItem(
            type=MessageItemType.TEXT,
            text_item=TextItem(text="mid"),
            ref_msg=RefMessage(message_item=inner),
        )
        outer = MessageItem(
            type=MessageItemType.TEXT,
            text_item=TextItem(text="outer"),
            ref_msg=RefMessage(message_item=mid, title="mid_title"),
        )
        result = body_from_item_list([outer])
        assert "outer" in result
        assert len(result) > 0


# ---------------------------------------------------------------------------
# context_token_store — 基础 CRUD
# ---------------------------------------------------------------------------

class TestContextTokenStore:
    def test_set_and_get(self):
        set_context_token("acct1", "user1", "tok_abc")
        assert get_context_token("acct1", "user1") == "tok_abc"

    def test_get_missing_returns_none(self):
        assert get_context_token("acct1", "user_nonexistent") is None

    def test_overwrite_updates_value(self):
        set_context_token("acct1", "user1", "old_token")
        set_context_token("acct1", "user1", "new_token")
        assert get_context_token("acct1", "user1") == "new_token"

    def test_different_accounts_isolated(self):
        set_context_token("acct1", "user1", "tok_a1")
        set_context_token("acct2", "user1", "tok_a2")
        assert get_context_token("acct1", "user1") == "tok_a1"
        assert get_context_token("acct2", "user1") == "tok_a2"

    def test_different_users_isolated(self):
        set_context_token("acct1", "user1", "tok_u1")
        set_context_token("acct1", "user2", "tok_u2")
        assert get_context_token("acct1", "user1") == "tok_u1"
        assert get_context_token("acct1", "user2") == "tok_u2"

    def test_empty_token_stored(self):
        set_context_token("acct1", "user1", "")
        assert get_context_token("acct1", "user1") == ""

    def test_unicode_token_stored(self):
        token = "tok_中文_🔑_token"
        set_context_token("acct1", "user1", token)
        assert get_context_token("acct1", "user1") == token


# ---------------------------------------------------------------------------
# context_token_store — 压力测试
# ---------------------------------------------------------------------------

class TestContextTokenStoreStress:
    def test_many_users(self):
        """为 5000 个不同用户存储 token，全部可正确读回，应在 1s 内完成。"""
        import time
        n = 5000
        start = time.monotonic()
        for i in range(n):
            set_context_token("acct1", f"user{i}", f"tok{i}")
        for i in range(n):
            assert get_context_token("acct1", f"user{i}") == f"tok{i}"
        elapsed = time.monotonic() - start
        assert elapsed < 1.0, f"5000 用户读写耗时 {elapsed:.3f}s 超过 1s"

    def test_many_accounts_many_users(self):
        """10 个账号 × 100 个用户，共 1000 条记录不相互干扰，应在 500ms 内完成。"""
        import time
        start = time.monotonic()
        for a in range(10):
            for u in range(100):
                set_context_token(f"acct{a}", f"user{u}", f"tok_{a}_{u}")
        for a in range(10):
            for u in range(100):
                expected = f"tok_{a}_{u}"
                actual = get_context_token(f"acct{a}", f"user{u}")
                assert actual == expected, f"acct{a} user{u}: got {actual!r}"
        elapsed = time.monotonic() - start
        assert elapsed < 0.5, f"10×100 读写耗时 {elapsed:.3f}s 超过 500ms"


# ---------------------------------------------------------------------------
# context_token_store — 并发写入（真正的 asyncio.gather 并发）
# ---------------------------------------------------------------------------

class TestContextTokenStoreConcurrent:
    @pytest.mark.asyncio
    async def test_concurrent_writes_all_land(self):
        """N 个协程并发写入同一账号的不同用户，全部结果应正确落盘。"""
        n = 200

        async def write_one(i: int):
            set_context_token("acct_concurrent", f"user{i}", f"tok_c{i}")

        await asyncio.gather(*[write_one(i) for i in range(n)])

        for i in range(n):
            assert get_context_token("acct_concurrent", f"user{i}") == f"tok_c{i}"

    @pytest.mark.asyncio
    async def test_concurrent_overwrites_last_writer_wins(self):
        """同一 (account, user) 被并发写入多次，最终值为某个合法写入（不丢失、不崩溃）。"""
        results = []

        async def write_version(v: int):
            set_context_token("acct_ow", "same_user", f"tok_v{v}")

        await asyncio.gather(*[write_version(i) for i in range(50)])

        final = get_context_token("acct_ow", "same_user")
        # 最终值必须是某次合法写入的值
        assert final is not None
        assert final.startswith("tok_v")

    @pytest.mark.asyncio
    async def test_concurrent_reads_consistent(self):
        """写入后并发读取，所有读取结果应一致。"""
        set_context_token("acct_rd", "user1", "consistent_token")

        async def read_one() -> str:
            return get_context_token("acct_rd", "user1")

        results = await asyncio.gather(*[read_one() for _ in range(100)])
        assert all(r == "consistent_token" for r in results)

    @pytest.mark.asyncio
    async def test_concurrent_multi_account_no_cross_contamination(self):
        """并发向 20 个账号各写入 10 个用户，不同账号间不应相互污染。"""
        async def write_all(acct: int):
            for u in range(10):
                set_context_token(f"acct_{acct}", f"user_{u}", f"v_{acct}_{u}")

        await asyncio.gather(*[write_all(a) for a in range(20)])

        for a in range(20):
            for u in range(10):
                assert get_context_token(f"acct_{a}", f"user_{u}") == f"v_{a}_{u}"
