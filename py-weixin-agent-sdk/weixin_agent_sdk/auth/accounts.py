"""
微信账号存储与读取。

目录结构（位于 ~/.openclaw/openclaw-weixin/）：
  accounts.json          账号 ID 索引
  accounts/<id>.json     每账号凭据（token、baseUrl、userId）
"""

from __future__ import annotations

import datetime
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from weixin_agent_sdk.api.types import WeixinAccountFile
from weixin_agent_sdk.storage.state_dir import resolve_state_dir
from weixin_agent_sdk.storage.sync_buf import atomic_write_json
from weixin_agent_sdk.util.logger import logger

DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"


# ---------------------------------------------------------------------------
# ID 规范化
# ---------------------------------------------------------------------------

def normalize_account_id(raw: str) -> str:
    """将账号 ID 转换为文件系统安全字符串（小写、@ 和 . 替换为 -）。"""
    return re.sub(r"[@.]", "-", raw.strip().lower())


def derive_raw_account_id(normalized_id: str) -> str | None:
    """
    将已知后缀的规范化 ID 反推回原始 ID（兼容旧格式文件名）。
    例：'b0f5860fdecb-im-bot' -> 'b0f5860fdecb@im.bot'
    """
    if normalized_id.endswith("-im-bot"):
        return f"{normalized_id[:-7]}@im.bot"
    if normalized_id.endswith("-im-wechat"):
        return f"{normalized_id[:-10]}@im.wechat"
    return None


# ---------------------------------------------------------------------------
# 路径解析
# ---------------------------------------------------------------------------

def weixin_state_dir() -> Path:
    return resolve_state_dir() / "openclaw-weixin"


def account_index_path() -> Path:
    return weixin_state_dir() / "accounts.json"


def accounts_dir() -> Path:
    return weixin_state_dir() / "accounts"


def account_path(account_id: str) -> Path:
    return accounts_dir() / f"{account_id}.json"


# ---------------------------------------------------------------------------
# 账号索引（全局 accounts.json）
# ---------------------------------------------------------------------------

def list_indexed_weixin_account_ids() -> list[str]:
    """返回通过扫码登录注册的所有账号 ID。"""
    path = account_index_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        logger.warn(f"list_indexed_weixin_account_ids: 无法读取索引文件 {path}: {exc}")
        return []

    try:
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            raise json.JSONDecodeError("根节点不是列表", text, 0)
        return [s for s in parsed if isinstance(s, str) and s.strip()]
    except json.JSONDecodeError as exc:
        corrupt_path = path.with_suffix(".corrupt")
        logger.warn(
            f"list_indexed_weixin_account_ids: 索引文件 {path} JSON 损坏，"
            f"已重命名为 {corrupt_path}: {exc}"
        )
        try:
            path.rename(corrupt_path)
        except OSError as rename_exc:
            logger.warn(f"list_indexed_weixin_account_ids: 重命名失败: {rename_exc}")
        return []


def register_weixin_account_id(account_id: str) -> None:
    """
    将 account_id 写入索引文件，与已有账号合并去重（单账号模式覆盖之前记录）。
    使用原子写入防止崩溃损坏索引。
    """
    weixin_state_dir().mkdir(parents=True, exist_ok=True)
    existing = list_indexed_weixin_account_ids()
    # 去重：先移除同 ID 旧记录，再插入头部（最近登录的排首位）
    merged = [account_id] + [aid for aid in existing if aid != account_id]
    atomic_write_json(account_index_path(), merged)


def list_weixin_account_ids() -> list[str]:
    """同 list_indexed_weixin_account_ids，对外公开别名。"""
    return list_indexed_weixin_account_ids()


# ---------------------------------------------------------------------------
# 账号凭据文件（每账号独立 JSON）
# ---------------------------------------------------------------------------

@dataclass
class WeixinAccountData:
    """每账号持久化数据。"""
    token: str | None = None
    saved_at: str | None = None
    base_url: str | None = None
    user_id: str | None = None


def read_account_file(path: Path) -> WeixinAccountData | None:
    """
    读取账号凭据文件。

    文件不存在返回 None；JSON 损坏时记录 warn 并将文件重命名为 .corrupt 备份，
    避免损坏文件导致账号被误判为不存在而触发重新登录。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warn(f"read_account_file: 无法读取 {path}: {exc}")
        return None

    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        corrupt_path = path.with_suffix(".corrupt")
        logger.warn(f"read_account_file: {path} JSON 损坏，已重命名为 {corrupt_path}: {exc}")
        try:
            path.rename(corrupt_path)
        except OSError as rename_exc:
            logger.warn(f"read_account_file: 重命名失败: {rename_exc}")
        return None

    if not isinstance(raw, dict):
        logger.warn(f"read_account_file: {path} 格式非法（根节点不是对象），返回 None")
        return None

    return WeixinAccountData(
        token=raw.get("token"),
        saved_at=raw.get("savedAt"),
        base_url=raw.get("baseUrl"),
        user_id=raw.get("userId"),
    )


def load_legacy_token() -> str | None:
    """读取旧版单账号 credentials.json（兼容旧安装）。"""
    legacy = resolve_state_dir() / "credentials" / "openclaw-weixin" / "credentials.json"
    try:
        if not legacy.exists():
            return None
        raw: Any = json.loads(legacy.read_text(encoding="utf-8"))
        token = raw.get("token")
        if isinstance(token, str):
            logger.info(f"load_legacy_token: 从旧版凭据文件迁移 token，路径={legacy}")
            return token
    except Exception as exc:
        logger.warn(f"load_legacy_token: 读取旧版凭据失败（忽略）: {exc}")
    return None


def load_weixin_account(account_id: str) -> WeixinAccountData | None:
    """
    按优先级加载账号数据：
      1. 规范化 ID 对应的文件
      2. 旧原始 ID 对应的文件（兼容旧安装）
      3. 旧版单账号 credentials.json
    """
    primary = read_account_file(account_path(account_id))
    if primary:
        return primary

    raw_id = derive_raw_account_id(account_id)
    if raw_id:
        compat = read_account_file(account_path(raw_id))
        if compat:
            return compat

    token = load_legacy_token()
    if token:
        return WeixinAccountData(token=token)

    return None


def save_weixin_account(account_id: str, update: WeixinAccountData) -> None:
    """
    持久化账号数据（与现有文件合并），使用原子写入。

    update 中非 None 的字段覆盖现有值；None 字段保留现有值。
    """
    accounts_dir().mkdir(parents=True, exist_ok=True)

    existing = load_weixin_account(account_id) or WeixinAccountData()

    token = (update.token or "").strip() or existing.token

    base_url = (update.base_url or "").strip() or existing.base_url

    if update.user_id is not None:
        user_id = update.user_id.strip() or None
    else:
        user_id = (existing.user_id or "").strip() or None

    saved_at = datetime.datetime.now(datetime.timezone.utc).isoformat() if token else None

    file_data = WeixinAccountFile(
        token=token,
        saved_at=saved_at,
        base_url=base_url,
        user_id=user_id,
    )

    file_path = account_path(account_id)
    atomic_write_json(file_path, file_data.to_dict())
    try:
        os.chmod(file_path, 0o600)
    except Exception:
        pass  # Windows 不支持 chmod，尽力而为


def clear_weixin_account(account_id: str) -> None:
    """删除账号凭据文件。"""
    try:
        account_path(account_id).unlink()
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warn(f"clear_weixin_account: 删除账号文件失败 account_id={account_id}: {exc}")


# ---------------------------------------------------------------------------
# openclaw.json 路由标签
# ---------------------------------------------------------------------------

def resolve_config_path() -> Path:
    env_path = os.environ.get("OPENCLAW_CONFIG", "").strip()
    if env_path:
        return Path(env_path)
    return resolve_state_dir() / "openclaw.json"


def load_config_route_tag(account_id: str | None = None) -> str | None:
    """
    从 openclaw.json 读取 SKRouteTag。

    查找顺序：
      channels.openclaw-weixin.accounts.<accountId>.routeTag（精确匹配）
      channels.openclaw-weixin.routeTag（段级配置）
    """
    try:
        config_path = resolve_config_path()
        if not config_path.exists():
            return None
        cfg: Any = json.loads(config_path.read_text(encoding="utf-8"))
        section = (cfg.get("channels") or {}).get("openclaw-weixin")
        if not isinstance(section, dict):
            return None
        if account_id:
            accounts = section.get("accounts")
            if isinstance(accounts, dict):
                per_account = accounts.get(account_id)
                if isinstance(per_account, dict):
                    tag = per_account.get("routeTag")
                    if isinstance(tag, int):
                        return str(tag)
                    if isinstance(tag, str) and tag.strip():
                        return tag.strip()
        tag = section.get("routeTag")
        if isinstance(tag, int):
            return str(tag)
        if isinstance(tag, str) and tag.strip():
            return tag.strip()
    except (json.JSONDecodeError, OSError, KeyError, TypeError):
        pass
    return None


# ---------------------------------------------------------------------------
# 账号解析（合并配置 + 凭据）
# ---------------------------------------------------------------------------

@dataclass
class ResolvedWeixinAccount:
    """完整解析后的账号信息（凭据 + 路由配置）。"""
    account_id: str
    base_url: str
    cdn_base_url: str
    token: str | None
    enabled: bool
    configured: bool  # 已通过扫码登录获得 token


def resolve_weixin_account(account_id: str | None) -> ResolvedWeixinAccount:
    """
    根据 account_id 解析账号，读取已存储的凭据。

    account_id 为空时抛出 ValueError。
    """
    raw = (account_id or "").strip()
    if not raw:
        raise ValueError("weixin: account_id is required (no default account)")

    normalized = normalize_account_id(raw)
    data = load_weixin_account(normalized)

    token = None
    if data and data.token:
        token = data.token.strip() or None

    state_base_url = (data.base_url or "").strip() if data else ""

    return ResolvedWeixinAccount(
        account_id=normalized,
        base_url=state_base_url or DEFAULT_BASE_URL,
        cdn_base_url=CDN_BASE_URL,
        token=token,
        enabled=True,
        configured=bool(token),
    )
