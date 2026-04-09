"""
调试模式开关，状态持久化到磁盘，网关重启后不丢失。

状态文件：<stateDir>/openclaw-weixin/debug-mode.json
格式：{"accounts": {"<accountId>": true, ...}}

启用后，process_one_message 在每条 AI 回复发出后追加全链路耗时摘要。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from weixin_agent_sdk.storage.state_dir import resolve_state_dir
from weixin_agent_sdk.storage.sync_buf import atomic_write_json
from weixin_agent_sdk.util.logger import logger


@dataclass
class DebugModeState:
    """调试模式持久化状态。accounts 映射 accountId → 是否开启。"""
    accounts: dict[str, bool] = field(default_factory=dict)


def resolve_debug_mode_path() -> Path:
    """返回调试模式状态文件路径。"""
    return resolve_state_dir() / "openclaw-weixin" / "debug-mode.json"


def load_state() -> DebugModeState:
    """从磁盘加载调试模式状态，文件缺失或损坏时返回空状态。"""
    try:
        raw = resolve_debug_mode_path().read_text(encoding="utf-8")
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and isinstance(parsed.get("accounts"), dict):
            return DebugModeState(accounts=parsed["accounts"])
    except Exception:
        pass
    return DebugModeState()


def save_state(state: DebugModeState) -> None:
    """将调试模式状态原子写入磁盘（先写临时文件再重命名，防止数据损坏）。"""
    atomic_write_json(resolve_debug_mode_path(), {"accounts": state.accounts})


def toggle_debug_mode(account_id: str) -> bool:
    """切换指定账号的调试模式，返回切换后的新状态。"""
    state = load_state()
    next_state = not state.accounts.get(account_id, False)
    state.accounts[account_id] = next_state
    try:
        save_state(state)
    except Exception as exc:
        logger.error(f"debug-mode: 持久化状态失败: {exc}")
    return next_state


def is_debug_mode(account_id: str) -> bool:
    """检查指定账号是否已开启调试模式。"""
    return load_state().accounts.get(account_id, False) is True
