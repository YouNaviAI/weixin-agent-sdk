"""随机 ID 和临时文件名生成工具。"""

import secrets
import time


def generate_id(prefix: str) -> str:
    """
    生成带前缀的唯一 ID，使用时间戳加随机十六进制字节。
    格式：{prefix}:{timestamp_ms}-{8位十六进制}
    """
    timestamp_ms = int(time.time() * 1000)
    rand_hex = secrets.token_hex(4)
    return f"{prefix}:{timestamp_ms}-{rand_hex}"


def temp_file_name(prefix: str, ext: str) -> str:
    """
    生成带随机后缀的临时文件名。
    格式：{prefix}-{timestamp_ms}-{8位十六进制}{ext}

    `ext` 必须包含前导点，例如 '.png'、'.mp4'。
    """
    if ext and not ext.startswith("."):
        raise ValueError(f"ext 必须以 '.' 开头，收到 {ext!r}")
    timestamp_ms = int(time.time() * 1000)
    rand_hex = secrets.token_hex(4)
    return f"{prefix}-{timestamp_ms}-{rand_hex}{ext}"
