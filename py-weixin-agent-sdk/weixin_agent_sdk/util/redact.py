"""日志输出中敏感信息的脱敏工具函数。"""

from urllib.parse import urlparse

DEFAULT_BODY_MAX_LEN = 200
DEFAULT_TOKEN_PREFIX_LEN = 6


def truncate(s: str | None, max_len: int) -> str:
    """截断字符串，超出时附加长度提示。"""
    if not s:
        return ""
    if len(s) <= max_len:
        return s
    return f"{s[:max_len]}…(len={len(s)})"


def redact_token(token: str | None, prefix_len: int = DEFAULT_TOKEN_PREFIX_LEN) -> str:
    """脱敏 token/密钥：只显示前几个字符和总长度。"""
    if not token:
        return "(none)"
    if len(token) <= prefix_len:
        return f"****(len={len(token)})"
    return f"{token[:prefix_len]}…(len={len(token)})"


def redact_body(body: str | None, max_len: int = DEFAULT_BODY_MAX_LEN) -> str:
    """截断 JSON 请求体字符串以便安全记录日志。"""
    if not body:
        return "(empty)"
    if len(body) <= max_len:
        return body
    return f"{body[:max_len]}…(truncated, totalLen={len(body)})"


def redact_url(raw_url: str) -> str:
    """去除 URL 中的查询字符串（通常包含签名/令牌）。"""
    parsed = urlparse(raw_url)
    # 防范无 scheme 的 URL 导致 netloc 为空的情况。
    if not parsed.scheme or not parsed.netloc:
        return truncate(raw_url, 80)
    base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    return f"{base}?<redacted>" if parsed.query else base
