"""
get_updates_buf 的持久化存储（断点续传）。

buf 以 JSON 格式存储于：
  ~/.openclaw/openclaw-weixin/accounts/{account_id}.sync.json

写入时使用原子重命名，防止崩溃导致数据损坏。
"""

import dataclasses
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from weixin_agent_sdk.storage.state_dir import resolve_state_dir

SAFE_ACCOUNT_ID_RE = re.compile(r"^[\w\-@.]+$")


@dataclass
class SyncBufData:
    get_updates_buf: str


def resolve_accounts_dir() -> Path:
    return resolve_state_dir() / "openclaw-weixin" / "accounts"


def get_sync_buf_file_path(account_id: str) -> Path:
    """返回指定账号的 sync buf 文件路径。

    若 account_id 包含路径穿越字符则抛出 ValueError。
    """
    if not SAFE_ACCOUNT_ID_RE.match(account_id):
        raise ValueError(f"Unsafe account_id: {account_id!r}")
    return resolve_accounts_dir() / f"{account_id}.sync.json"


def read_sync_buf_file(file_path: Path) -> SyncBufData | None:
    """从 JSON 文件读取 get_updates_buf，文件不存在时返回 None。"""
    try:
        raw = file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None

    data = json.loads(raw)  # JSONDecodeError 向上传播 — 文件损坏应暴露给调用方。
    if not isinstance(data, dict):
        return None
    value = data.get("get_updates_buf")
    if isinstance(value, str):
        return SyncBufData(get_updates_buf=value)
    return None


def load_get_updates_buf(file_path: Path) -> SyncBufData | None:
    """
    加载已持久化的 get_updates_buf。

    按以下顺序回退：
      1. 主路径（新安装使用的 file_path）
      2. 旧版单账号路径（不支持多账号的极旧版本安装）

    注意：旧版使用 '@' 的文件名兼容路径由 auth/accounts.py 在调用此函数时传入正确路径处理。
    """
    result = read_sync_buf_file(file_path)
    if result is not None:
        return result

    legacy_path = (
        resolve_state_dir()
        / "agents"
        / "default"
        / "sessions"
        / ".openclaw-weixin-sync"
        / "default.json"
    )
    try:
        return read_sync_buf_file(legacy_path)
    except json.JSONDecodeError:
        return None  # 旧版路径为尽力而为；损坏数据静默跳过。


def atomic_write_json(path: Path, data: object) -> None:
    """
    原子写入任意 JSON 数据（先写临时文件再重命名），防止进程崩溃导致数据损坏。
    自动创建父目录，失败时清理临时文件。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    try:
        tmp_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def save_get_updates_buf(file_path: Path, get_updates_buf: str) -> None:
    """
    原子写入 get_updates_buf（先写临时文件再重命名）。
    自动创建父目录，失败时清理临时文件。
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = file_path.with_suffix(".tmp")
    try:
        payload = json.dumps(dataclasses.asdict(SyncBufData(get_updates_buf=get_updates_buf)), separators=(",", ":"))
        tmp_path.write_text(payload, encoding="utf-8")
        os.replace(tmp_path, file_path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise
