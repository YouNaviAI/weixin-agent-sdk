"""
weixin_agent_sdk.messaging.debug_mode 的测试用例。

覆盖：
  - DebugModeState：dataclass 默认值
  - load_state：文件缺失返回空状态、损坏 JSON 返回空状态
  - save_state / load_state：往返一致性
  - toggle_debug_mode：初始关→开→关切换
  - is_debug_mode：读取状态
  - 多账号隔离
  - 压力测试：1000 次 toggle
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from weixin_agent_sdk.messaging.debug_mode import (
    DebugModeState,
    is_debug_mode,
    load_state,
    resolve_debug_mode_path,
    save_state,
    toggle_debug_mode,
)


# ---------------------------------------------------------------------------
# Fixture：将状态文件重定向到临时目录
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_state_dir(tmp_path):
    """
    每个测试使用独立的临时目录，避免影响真实磁盘状态。
    通过 patch resolve_state_dir 实现路径重定向。
    """
    with patch(
        "weixin_agent_sdk.messaging.debug_mode.resolve_state_dir",
        return_value=tmp_path,
    ):
        yield tmp_path


# ---------------------------------------------------------------------------
# DebugModeState dataclass
# ---------------------------------------------------------------------------

class TestDebugModeState:
    def test_default_accounts_empty(self):
        state = DebugModeState()
        assert state.accounts == {}

    def test_custom_accounts(self):
        state = DebugModeState(accounts={"acct1": True, "acct2": False})
        assert state.accounts["acct1"] is True
        assert state.accounts["acct2"] is False

    def test_instances_are_independent(self):
        """两个 DebugModeState 实例共享默认工厂 dict 不能相互干扰。"""
        s1 = DebugModeState()
        s2 = DebugModeState()
        s1.accounts["acct1"] = True
        assert "acct1" not in s2.accounts


# ---------------------------------------------------------------------------
# load_state — 文件不存在 / 损坏
# ---------------------------------------------------------------------------

class TestLoadState:
    def test_missing_file_returns_empty(self):
        """状态文件不存在时应返回空状态，不抛出异常。"""
        state = load_state()
        assert state.accounts == {}

    def test_corrupted_json_returns_empty(self, tmp_path):
        """损坏的 JSON 文件应静默返回空状态。"""
        path = resolve_debug_mode_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("NOT_JSON", encoding="utf-8")
        state = load_state()
        assert state.accounts == {}

    def test_missing_accounts_key_returns_empty(self, tmp_path):
        """缺少 accounts 字段时应静默返回空状态。"""
        path = resolve_debug_mode_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"other": "data"}), encoding="utf-8")
        state = load_state()
        assert state.accounts == {}

    def test_wrong_accounts_type_returns_empty(self, tmp_path):
        """accounts 字段不是 dict 时应静默返回空状态。"""
        path = resolve_debug_mode_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"accounts": [1, 2, 3]}), encoding="utf-8")
        state = load_state()
        assert state.accounts == {}


# ---------------------------------------------------------------------------
# save_state / load_state — 往返
# ---------------------------------------------------------------------------

class TestSaveLoadRoundTrip:
    def test_basic_roundtrip(self):
        """保存后重新加载应得到相同状态。"""
        state = DebugModeState(accounts={"acct1": True, "acct2": False})
        save_state(state)
        loaded = load_state()
        assert loaded.accounts == {"acct1": True, "acct2": False}

    def test_empty_roundtrip(self):
        save_state(DebugModeState())
        loaded = load_state()
        assert loaded.accounts == {}

    def test_overwrite_roundtrip(self):
        """多次保存，最后一次的状态应生效。"""
        save_state(DebugModeState(accounts={"acct1": True}))
        save_state(DebugModeState(accounts={"acct1": False}))
        loaded = load_state()
        assert loaded.accounts["acct1"] is False

    def test_file_is_valid_json(self):
        """保存后文件应为有效 JSON。"""
        save_state(DebugModeState(accounts={"acct1": True}))
        path = resolve_debug_mode_path()
        raw = path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
        assert isinstance(parsed, dict)
        assert "accounts" in parsed


# ---------------------------------------------------------------------------
# toggle_debug_mode
# ---------------------------------------------------------------------------

class TestToggleDebugMode:
    def test_default_is_false(self):
        assert is_debug_mode("acct1") is False

    def test_first_toggle_enables(self):
        result = toggle_debug_mode("acct1")
        assert result is True

    def test_second_toggle_disables(self):
        toggle_debug_mode("acct1")
        result = toggle_debug_mode("acct1")
        assert result is False

    def test_toggle_persists(self):
        """toggle 后调用 is_debug_mode 应反映新状态。"""
        toggle_debug_mode("acct1")
        assert is_debug_mode("acct1") is True

    def test_toggle_returns_new_state(self):
        """返回值应是切换后的值。"""
        r1 = toggle_debug_mode("acct1")
        r2 = toggle_debug_mode("acct1")
        assert r1 is True
        assert r2 is False

    def test_toggle_nonexistent_account(self):
        """从未设置过的账号应视作 False，toggle 后变 True。"""
        result = toggle_debug_mode("new_account_xyz")
        assert result is True


# ---------------------------------------------------------------------------
# 多账号隔离
# ---------------------------------------------------------------------------

class TestMultiAccountIsolation:
    def test_toggle_one_account_does_not_affect_other(self):
        toggle_debug_mode("acct1")
        assert is_debug_mode("acct2") is False

    def test_independent_toggle_states(self):
        toggle_debug_mode("acct1")
        toggle_debug_mode("acct2")
        toggle_debug_mode("acct2")
        assert is_debug_mode("acct1") is True
        assert is_debug_mode("acct2") is False

    def test_many_accounts_independent(self):
        """10 个账号独立开关，互不影响。"""
        for i in range(10):
            if i % 2 == 0:
                toggle_debug_mode(f"acct{i}")
        for i in range(10):
            expected = (i % 2 == 0)
            assert is_debug_mode(f"acct{i}") == expected


# ---------------------------------------------------------------------------
# 压力测试
# ---------------------------------------------------------------------------

class TestDebugModeStress:
    def test_many_toggles(self):
        """1000 次 toggle 应保持状态一致，奇数次 = True，偶数次 = False。"""
        import time
        start = time.monotonic()
        for i in range(1000):
            toggle_debug_mode("acct_stress")
        elapsed = time.monotonic() - start
        # 偶数次 toggle 后应回到 False
        assert is_debug_mode("acct_stress") is False
        assert elapsed < 5.0, f"1000 次 toggle 耗时 {elapsed:.3f}s 超过 5s"

    def test_many_accounts_roundtrip(self):
        """100 个账号各开启一次，全部可正确读回。"""
        for i in range(100):
            toggle_debug_mode(f"bulk_acct{i}")
        for i in range(100):
            assert is_debug_mode(f"bulk_acct{i}") is True
