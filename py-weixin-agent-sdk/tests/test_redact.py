"""weixin_agent_sdk.util.redact 的测试用例。"""

from weixin_agent_sdk.util.redact import (
    redact_body,
    redact_token,
    redact_url,
    truncate,
)


# ---------------------------------------------------------------------------
# truncate
# ---------------------------------------------------------------------------

class TestTruncate:
    def test_short_string_unchanged(self):
        assert truncate("hello", 10) == "hello"

    def test_exact_length_unchanged(self):
        assert truncate("hello", 5) == "hello"

    def test_long_string_truncated(self):
        result = truncate("abcdefgh", 4)
        assert result.startswith("abcd")
        assert "len=8" in result

    def test_none_returns_empty(self):
        assert truncate(None, 10) == ""

    def test_empty_string_returns_empty(self):
        assert truncate("", 10) == ""

    def test_unicode_not_broken(self):
        s = "你好世界！"
        result = truncate(s, 3)
        assert result.startswith("你好世")
        assert "len=5" in result


# ---------------------------------------------------------------------------
# redact_token
# ---------------------------------------------------------------------------

class TestRedactToken:
    def test_none_returns_none_label(self):
        assert redact_token(None) == "(none)"

    def test_empty_returns_none_label(self):
        assert redact_token("") == "(none)"

    def test_short_token_fully_redacted(self):
        result = redact_token("abc", prefix_len=6)
        assert "****" in result
        assert "len=3" in result

    def test_token_shows_prefix(self):
        result = redact_token("abcdefghij", prefix_len=4)
        assert result.startswith("abcd")
        assert "len=10" in result

    def test_exact_prefix_length_fully_redacted(self):
        result = redact_token("abcdef", prefix_len=6)
        assert "****" in result

    def test_default_prefix_len(self):
        result = redact_token("abcdefghij")
        assert result.startswith("abcdef")


# ---------------------------------------------------------------------------
# redact_body
# ---------------------------------------------------------------------------

class TestRedactBody:
    def test_none_returns_empty_label(self):
        assert redact_body(None) == "(empty)"

    def test_empty_returns_empty_label(self):
        assert redact_body("") == "(empty)"

    def test_short_body_unchanged(self):
        body = '{"key": "value"}'
        assert redact_body(body) == body

    def test_long_body_truncated(self):
        body = "x" * 300
        result = redact_body(body)
        assert len(result) < len(body)
        assert "truncated" in result
        assert "totalLen=300" in result

    def test_exact_max_len_unchanged(self):
        body = "x" * 200
        assert redact_body(body) == body


# ---------------------------------------------------------------------------
# redact_url
# ---------------------------------------------------------------------------

class TestRedactUrl:
    def test_url_with_query_redacted(self):
        url = "https://example.com/path?token=secret&sig=abc"
        result = redact_url(url)
        assert "secret" not in result
        assert "example.com/path" in result
        assert "<redacted>" in result

    def test_url_without_query_unchanged(self):
        url = "https://example.com/path"
        result = redact_url(url)
        assert result == url

    def test_schemeless_url_truncated(self):
        result = redact_url("no-scheme-url")
        assert "://" not in result

    def test_empty_scheme_truncated(self):
        result = redact_url("//example.com/path")
        assert result  # doesn't crash

    def test_invalid_url_falls_back_to_truncate(self):
        # Should not raise, just truncate
        result = redact_url("not a url at all !!!")
        assert isinstance(result, str)

    def test_http_scheme(self):
        url = "http://api.example.com/upload?key=secret"
        result = redact_url(url)
        assert result.startswith("http://api.example.com")
        assert "secret" not in result

    def test_path_preserved(self):
        url = "https://cdn.example.com/media/upload/file.jpg?sig=xyz"
        result = redact_url(url)
        assert "/media/upload/file.jpg" in result
