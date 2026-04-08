"""
配对逻辑：管理扫码授权的用户白名单（allowFrom 列表）。

文件路径格式：
  <credDir>/openclaw-weixin-<accountId>-allowFrom.json
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from weixin_agent_sdk.storage.state_dir import resolve_state_dir
from weixin_agent_sdk.storage.sync_buf import atomic_write_json
from weixin_agent_sdk.util.logger import logger


# ---------------------------------------------------------------------------
# 序列化结构
# ---------------------------------------------------------------------------

@dataclass
class AllowFromFile:
    """allowFrom JSON 文件的完整结构。"""
    version: int
    allow_from: list[str]

    def to_dict(self) -> Any:
        return {"version": self.version, "allowFrom": self.allow_from}

    @classmethod
    def from_dict(cls, raw: object) -> AllowFromFile:
        if not isinstance(raw, dict):
            return cls(version=1, allow_from=[])
        allow_from = raw.get("allowFrom")
        if not isinstance(allow_from, list):
            allow_from = []
        return cls(
            version=int(raw.get("version", 1)),
            allow_from=[uid for uid in allow_from if isinstance(uid, str) and uid.strip()],
        )


# ---------------------------------------------------------------------------
# 路径工具
# ---------------------------------------------------------------------------

def resolve_credentials_dir() -> Path:
    """
    凭据目录：$OPENCLAW_OAUTH_DIR 或 <state_dir>/credentials。
    与核心框架 resolveOAuthDir 保持一致。
    """
    override = os.environ.get("OPENCLAW_OAUTH_DIR", "").strip()
    if override:
        return Path(override)
    return resolve_state_dir() / "credentials"


def safe_key(raw: str) -> str:
    """将渠道/账号键转换为文件名安全字符串（镜像核心 safeChannelKey）。"""
    trimmed = raw.strip().lower()
    if not trimmed:
        raise ValueError(f"invalid key for allowFrom path: {raw!r}")
    safe = re.sub(r'[\\/:*?"<>|]', "_", trimmed).replace("..", "_")
    if not safe or safe == "_":
        raise ValueError(f"invalid key for allowFrom path: {raw!r}")
    return safe


def resolve_framework_allow_from_path(account_id: str) -> Path:
    """
    返回指定账号的 allowFrom 文件路径。
    格式：<credDir>/openclaw-weixin-<accountId>-allowFrom.json
    """
    base = safe_key("openclaw-weixin")
    safe_account = safe_key(account_id)
    return resolve_credentials_dir() / f"{base}-{safe_account}-allowFrom.json"


# ---------------------------------------------------------------------------
# 白名单读写
# ---------------------------------------------------------------------------

def read_framework_allow_from_list(account_id: str) -> list[str]:
    """
    读取指定账号的授权用户 ID 列表。

    文件不存在返回空列表；解析失败记录 warn 并返回空列表
    （白名单损坏等同于全部用户失去授权，由调用方决定是否重新授权）。
    """
    file_path = resolve_framework_allow_from_path(account_id)
    try:
        text = file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        logger.warn(f"read_framework_allow_from_list: 无法读取 {file_path}: {exc}")
        return []

    try:
        return AllowFromFile.from_dict(json.loads(text)).allow_from
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warn(
            f"read_framework_allow_from_list: {file_path} JSON 损坏，返回空白名单: {exc}"
        )
        return []


def register_user_in_allow_from_store(account_id: str, user_id: str) -> bool:
    """
    将 user_id 添加到 account_id 对应的 allowFrom 白名单，使用原子写入。

    读取现有文件失败时抛出异常，防止因读取错误清空现有白名单。
    返回 True 表示列表发生变化，False 表示 user_id 已存在或无效。
    """
    trimmed_user_id = user_id.strip()
    if not trimmed_user_id:
        return False

    file_path = resolve_framework_allow_from_path(account_id)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    content = AllowFromFile(version=1, allow_from=[])
    try:
        text = file_path.read_text(encoding="utf-8")
        content = AllowFromFile.from_dict(json.loads(text))
    except FileNotFoundError:
        pass  # 文件不存在时从空列表开始，属于正常情况
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        # 读取/解析失败时抛出，防止覆盖现有白名单
        raise RuntimeError(
            f"register_user_in_allow_from_store: 无法读取白名单文件 {file_path}，"
            f"拒绝写入以避免数据丢失: {exc}"
        ) from exc

    if trimmed_user_id in content.allow_from:
        return False

    content.allow_from.append(trimmed_user_id)
    atomic_write_json(file_path, content.to_dict())
    logger.info(
        f"register_user_in_allow_from_store: added user_id={trimmed_user_id}"
        f" account_id={account_id} path={file_path}"
    )
    return True
