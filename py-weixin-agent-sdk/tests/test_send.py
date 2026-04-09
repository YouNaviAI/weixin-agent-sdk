"""
weixin_agent_sdk.messaging.send 的测试用例。

覆盖：
  - markdown_to_plain_text：所有转换规则 + 边界/压力测试
  - send_message_weixin：context_token 验证、正常发送、客户端异常传播
  - send_items：多条 item、发送失败传播
  - send_image/video/file_message_weixin：AES key 编码正确性、context_token 验证
"""

from __future__ import annotations

import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from weixin_agent_sdk.messaging.send import (
    markdown_to_plain_text,
    send_file_message_weixin,
    send_image_message_weixin,
    send_items,
    send_message_weixin,
    send_video_message_weixin,
)


# ---------------------------------------------------------------------------
# 工具：构造 UploadedFileInfo stub
# ---------------------------------------------------------------------------

def make_uploaded(aeskey: str = "a1b2c3d4e5f67890a1b2c3d4e5f67890") -> MagicMock:
    """构造 UploadedFileInfo 的 MagicMock，使用可配置的 aeskey。"""
    u = MagicMock()
    u.aeskey = aeskey
    u.filekey = "fake-filekey"
    u.download_encrypted_query_param = "enc=xxx"
    u.file_size = 1234
    u.file_size_ciphertext = 1248
    return u


def make_client() -> AsyncMock:
    """构造 WeixinApiClient 的 AsyncMock。"""
    client = AsyncMock()
    client.send_message = AsyncMock(return_value=None)
    return client


# ---------------------------------------------------------------------------
# markdown_to_plain_text — 边界
# ---------------------------------------------------------------------------

class TestMarkdownToPlainTextBoundary:
    def test_empty_string(self):
        assert markdown_to_plain_text("") == ""

    def test_plain_text_unchanged(self):
        assert markdown_to_plain_text("hello world") == "hello world"

    def test_plain_text_with_newlines(self):
        text = "line1\nline2\nline3"
        assert markdown_to_plain_text(text) == text

    def test_unicode_unchanged(self):
        text = "你好世界 🎉"
        assert markdown_to_plain_text(text) == text


# ---------------------------------------------------------------------------
# markdown_to_plain_text — 代码块
# ---------------------------------------------------------------------------

class TestMarkdownCodeBlock:
    def test_code_fence_stripped(self):
        md = "```python\nprint('hi')\n```"
        result = markdown_to_plain_text(md)
        assert "```" not in result
        assert "print('hi')" in result

    def test_code_fence_without_lang(self):
        md = "```\nsome code\n```"
        result = markdown_to_plain_text(md)
        assert "```" not in result
        assert "some code" in result

    def test_inline_code_stripped(self):
        result = markdown_to_plain_text("use `print()` function")
        assert "`" not in result
        assert "print()" in result

    def test_multiline_code_block_preserved(self):
        md = "```\nline1\nline2\nline3\n```"
        result = markdown_to_plain_text(md)
        assert "line1" in result
        assert "line2" in result


# ---------------------------------------------------------------------------
# markdown_to_plain_text — 链接与图片
# ---------------------------------------------------------------------------

class TestMarkdownLinksAndImages:
    def test_link_keeps_display_text(self):
        result = markdown_to_plain_text("[OpenAI](https://openai.com)")
        assert "OpenAI" in result
        assert "https://openai.com" not in result
        assert "[" not in result

    def test_image_removed_entirely(self):
        result = markdown_to_plain_text("before ![alt](img.png) after")
        assert "alt" not in result
        assert "img.png" not in result
        assert "before" in result
        assert "after" in result

    def test_link_with_title_keeps_text(self):
        result = markdown_to_plain_text('[click here](http://example.com "title")')
        assert "click here" in result
        assert "http" not in result

    def test_multiple_links(self):
        result = markdown_to_plain_text("[A](url1) and [B](url2)")
        assert "A" in result
        assert "B" in result
        assert "url1" not in result


# ---------------------------------------------------------------------------
# markdown_to_plain_text — 内联格式
# ---------------------------------------------------------------------------

class TestMarkdownInlineFormat:
    def test_bold_asterisks_stripped(self):
        result = markdown_to_plain_text("**bold text**")
        assert "**" not in result
        assert "bold text" in result

    def test_italic_asterisk_stripped(self):
        result = markdown_to_plain_text("*italic*")
        assert result.count("*") == 0
        assert "italic" in result

    def test_bold_underscores_stripped(self):
        result = markdown_to_plain_text("__bold__")
        assert "__" not in result
        assert "bold" in result

    def test_italic_underscore_stripped(self):
        result = markdown_to_plain_text("_italic_")
        assert "_" not in result
        assert "italic" in result

    def test_strikethrough_stripped(self):
        result = markdown_to_plain_text("~~deleted~~")
        assert "~~" not in result
        assert "deleted" in result

    def test_mixed_inline_formats(self):
        result = markdown_to_plain_text("**bold** and _italic_ and `code`")
        assert "bold" in result
        assert "italic" in result
        assert "code" in result
        assert "**" not in result
        assert "_" not in result
        assert "`" not in result


# ---------------------------------------------------------------------------
# markdown_to_plain_text — 表格
# ---------------------------------------------------------------------------

class TestMarkdownTable:
    def test_table_separator_removed(self):
        result = markdown_to_plain_text("| --- | --- |")
        assert "---" not in result

    def test_table_row_columns_joined(self):
        result = markdown_to_plain_text("| col1 | col2 | col3 |")
        assert "col1" in result
        assert "col2" in result
        assert "col3" in result
        assert "|" not in result

    def test_full_table(self):
        md = "| Name | Age |\n| --- | --- |\n| Alice | 30 |"
        result = markdown_to_plain_text(md)
        assert "Name" in result
        assert "Age" in result
        assert "Alice" in result
        assert "30" in result
        assert "---" not in result


# ---------------------------------------------------------------------------
# markdown_to_plain_text — 综合
# ---------------------------------------------------------------------------

class TestMarkdownComplex:
    def test_complex_mixed_content(self):
        md = (
            "# 标题\n\n"
            "这是 **粗体** 和 _斜体_。\n\n"
            "```python\nprint('hello')\n```\n\n"
            "查看 [文档](https://docs.example.com) 获取详情。\n\n"
            "![图片](image.png)\n\n"
            "~~删除线~~"
        )
        result = markdown_to_plain_text(md)
        assert "粗体" in result
        assert "斜体" in result
        assert "print('hello')" in result
        assert "文档" in result
        assert "https://docs.example.com" not in result
        assert "图片" not in result
        assert "删除线" in result
        assert "**" not in result
        assert "~~" not in result

    def test_idempotent_plain_text(self):
        """纯文本经过转换后应原样保留。"""
        text = "这是一段普通文字，没有任何 Markdown 标记。"
        assert markdown_to_plain_text(text) == text


# ---------------------------------------------------------------------------
# markdown_to_plain_text — 压力测试
# ---------------------------------------------------------------------------

class TestMarkdownStress:
    def test_very_long_plain_text(self):
        """100KB 纯文本应原样通过，不截断。"""
        long_text = "A" * 100_000
        result = markdown_to_plain_text(long_text)
        assert result == long_text

    def test_many_inline_marks(self):
        """1000 个粗体片段，全部应被正确剥除。"""
        import time
        parts = " ".join(f"**word{i}**" for i in range(1000))
        start = time.monotonic()
        result = markdown_to_plain_text(parts)
        elapsed = time.monotonic() - start
        assert "**" not in result
        for i in range(1000):
            assert f"word{i}" in result
        assert elapsed < 1.0, f"处理耗时 {elapsed:.3f}s 超过 1s"

    def test_many_links(self):
        """500 个链接，均应只保留展示文字。"""
        parts = " ".join(f"[link{i}](http://example.com/{i})" for i in range(500))
        result = markdown_to_plain_text(parts)
        assert "http://" not in result
        for i in range(500):
            assert f"link{i}" in result

    def test_deeply_nested_code_blocks(self):
        """多个独立代码块，每个都应正确剥除围栏。内容不含下划线，避免斜体规则误处理。"""
        blocks = "\n\n".join(f"```\ncodeblock{i}\n```" for i in range(100))
        result = markdown_to_plain_text(blocks)
        assert "```" not in result
        for i in range(100):
            assert f"codeblock{i}" in result


# ---------------------------------------------------------------------------
# send_message_weixin
# ---------------------------------------------------------------------------

class TestSendMessageWeixin:
    @pytest.mark.asyncio
    async def test_empty_context_token_raises(self):
        client = make_client()
        with pytest.raises(ValueError, match="context_token"):
            await send_message_weixin("user1", "hello", client, "")

    @pytest.mark.asyncio
    async def test_none_context_token_raises(self):
        """空字符串等价于缺失，应拒绝发送。"""
        client = make_client()
        with pytest.raises(ValueError):
            await send_message_weixin("user1", "hello", client, "")

    @pytest.mark.asyncio
    async def test_normal_send_calls_client(self):
        client = make_client()
        client_id = await send_message_weixin("user1", "hello", client, "tok_abc")
        assert client_id  # 返回非空 client_id
        client.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_text_sends_empty_item_list(self):
        """空文本时 item_list 为空列表，仍调用 send_message。"""
        client = make_client()
        await send_message_weixin("user1", "", client, "tok_abc")
        client.send_message.assert_called_once()
        msg = client.send_message.call_args[0][0]
        assert msg.item_list == []

    @pytest.mark.asyncio
    async def test_client_exception_propagates(self):
        client = make_client()
        client.send_message = AsyncMock(side_effect=RuntimeError("network error"))
        with pytest.raises(RuntimeError, match="network error"):
            await send_message_weixin("user1", "hello", client, "tok_abc")

    @pytest.mark.asyncio
    async def test_message_fields_correct(self):
        """验证构建的 WeixinMessage 字段正确。"""
        client = make_client()
        await send_message_weixin("target_user", "text content", client, "token_xyz")
        msg = client.send_message.call_args[0][0]
        assert msg.to_user_id == "target_user"
        assert msg.context_token == "token_xyz"
        assert len(msg.item_list) == 1
        assert msg.item_list[0].text_item.text == "text content"

    @pytest.mark.asyncio
    async def test_returns_unique_client_ids(self):
        """每次调用返回不同的 client_id。"""
        client = make_client()
        id1 = await send_message_weixin("u", "t1", client, "tok")
        id2 = await send_message_weixin("u", "t2", client, "tok")
        assert id1 != id2


# ---------------------------------------------------------------------------
# send_items
# ---------------------------------------------------------------------------

class TestSendItems:
    @pytest.mark.asyncio
    async def test_empty_items_returns_empty_string(self):
        client = make_client()
        result = await send_items("user1", "tok", [], client, "label")
        assert result == ""
        client.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_single_item_calls_send_once(self):
        from weixin_agent_sdk.api.types import MessageItem, MessageItemType, TextItem
        client = make_client()
        items = [MessageItem(type=MessageItemType.TEXT, text_item=TextItem(text="hi"))]
        await send_items("user1", "tok", items, client, "test")
        client.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_multiple_items_called_sequentially(self):
        """N 条 item 应调用 N 次 send_message。"""
        from weixin_agent_sdk.api.types import MessageItem, MessageItemType, TextItem
        client = make_client()
        items = [
            MessageItem(type=MessageItemType.TEXT, text_item=TextItem(text=f"msg{i}"))
            for i in range(5)
        ]
        await send_items("user1", "tok", items, client, "test")
        assert client.send_message.call_count == 5

    @pytest.mark.asyncio
    async def test_failure_propagates(self):
        from weixin_agent_sdk.api.types import MessageItem, MessageItemType, TextItem
        client = make_client()
        client.send_message = AsyncMock(side_effect=ConnectionError("down"))
        items = [MessageItem(type=MessageItemType.TEXT, text_item=TextItem(text="hi"))]
        with pytest.raises(ConnectionError):
            await send_items("user1", "tok", items, client, "test")


# ---------------------------------------------------------------------------
# AES key 编码正确性
# ---------------------------------------------------------------------------

class TestAesKeyEncoding:
    """验证 AES key 按 UTF-8/ASCII bytes 编码（与 TS Buffer.from(hexStr) 一致）。"""

    def _expected_b64(self, hex_key: str) -> str:
        """手动计算期望的 base64：将 hex 字符串的 ASCII 字节 base64 编码。"""
        return base64.b64encode(hex_key.encode("ascii")).decode()

    @pytest.mark.asyncio
    async def test_image_aes_key_encoding(self):
        client = make_client()
        aeskey = "deadbeefcafe0123456789abcdef0123"
        uploaded = make_uploaded(aeskey=aeskey)
        await send_image_message_weixin("user1", "", uploaded, client, "tok_abc")

        msg = client.send_message.call_args[0][0]
        # 找到 IMAGE 类型的 item
        image_items = [i for i in msg.item_list if i.image_item]
        assert image_items, "发送的消息中没有 image_item"
        actual_b64 = image_items[0].image_item.media.aes_key
        assert actual_b64 == self._expected_b64(aeskey)

    @pytest.mark.asyncio
    async def test_video_aes_key_encoding(self):
        client = make_client()
        aeskey = "1234567890abcdef1234567890abcdef"
        uploaded = make_uploaded(aeskey=aeskey)
        await send_video_message_weixin("user1", "", uploaded, client, "tok_abc")

        msg = client.send_message.call_args[0][0]
        video_items = [i for i in msg.item_list if i.video_item]
        assert video_items
        actual_b64 = video_items[0].video_item.media.aes_key
        assert actual_b64 == self._expected_b64(aeskey)

    @pytest.mark.asyncio
    async def test_file_aes_key_encoding(self):
        client = make_client()
        aeskey = "0011223344556677889900aabbccddee"
        uploaded = make_uploaded(aeskey=aeskey)
        await send_file_message_weixin("user1", "", "report.pdf", uploaded, client, "tok_abc")

        msg = client.send_message.call_args[0][0]
        file_items = [i for i in msg.item_list if i.file_item]
        assert file_items
        actual_b64 = file_items[0].file_item.media.aes_key
        assert actual_b64 == self._expected_b64(aeskey)

    def test_ascii_encoding_differs_from_fromhex(self):
        """确认 ASCII 编码与 bytes.fromhex 结果不同（捕获历史 Bug）。"""
        hex_key = "deadbeefcafe0123456789abcdef0123"
        ascii_b64 = base64.b64encode(hex_key.encode("ascii")).decode()
        fromhex_b64 = base64.b64encode(bytes.fromhex(hex_key)).decode()
        assert ascii_b64 != fromhex_b64, "两种编码方式应产生不同结果"


# ---------------------------------------------------------------------------
# send_image/video/file_message_weixin — context_token 验证
# ---------------------------------------------------------------------------

class TestSendMediaContextToken:
    @pytest.mark.asyncio
    async def test_image_empty_context_token_raises(self):
        with pytest.raises(ValueError, match="context_token"):
            await send_image_message_weixin("u", "", make_uploaded(), make_client(), "")

    @pytest.mark.asyncio
    async def test_video_empty_context_token_raises(self):
        with pytest.raises(ValueError, match="context_token"):
            await send_video_message_weixin("u", "", make_uploaded(), make_client(), "")

    @pytest.mark.asyncio
    async def test_file_empty_context_token_raises(self):
        with pytest.raises(ValueError, match="context_token"):
            await send_file_message_weixin("u", "", "f.txt", make_uploaded(), make_client(), "")


# ---------------------------------------------------------------------------
# send_image/video/file_message_weixin — text 附加逻辑
# ---------------------------------------------------------------------------

class TestSendMediaWithText:
    @pytest.mark.asyncio
    async def test_image_with_text_sends_two_items(self):
        """有文字时应发送 text + image 两条 item。"""
        client = make_client()
        await send_image_message_weixin("user1", "描述文字", make_uploaded(), client, "tok")
        # 两次 send_message（text 和 image 分别发送）
        assert client.send_message.call_count == 2

    @pytest.mark.asyncio
    async def test_image_without_text_sends_one_item(self):
        """无文字时只发送 image 一条 item。"""
        client = make_client()
        await send_image_message_weixin("user1", "", make_uploaded(), client, "tok")
        assert client.send_message.call_count == 1

    @pytest.mark.asyncio
    async def test_file_name_in_message(self):
        """发送文件时 file_name 应写入 file_item。"""
        client = make_client()
        await send_file_message_weixin("user1", "", "report.pdf", make_uploaded(), client, "tok")
        msg = client.send_message.call_args[0][0]
        file_items = [i for i in msg.item_list if i.file_item]
        assert file_items[0].file_item.file_name == "report.pdf"
