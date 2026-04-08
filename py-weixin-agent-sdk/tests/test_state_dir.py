"""weixin_agent_sdk.storage.state_dir 的测试用例。"""

from pathlib import Path

import pytest

from weixin_agent_sdk.storage.state_dir import resolve_state_dir


class TestResolveStateDir:
    def test_default_is_home_openclaw(self, monkeypatch):
        monkeypatch.delenv("OPENCLAW_STATE_DIR", raising=False)
        monkeypatch.delenv("CLAWDBOT_STATE_DIR", raising=False)
        result = resolve_state_dir()
        assert result == Path.home() / ".openclaw"

    def test_openclaw_env_var_takes_priority(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path / "custom"))
        monkeypatch.setenv("CLAWDBOT_STATE_DIR", str(tmp_path / "other"))
        result = resolve_state_dir()
        assert result == tmp_path / "custom"

    def test_clawdbot_env_var_used_as_fallback(self, monkeypatch, tmp_path):
        monkeypatch.delenv("OPENCLAW_STATE_DIR", raising=False)
        monkeypatch.setenv("CLAWDBOT_STATE_DIR", str(tmp_path / "clawdbot"))
        result = resolve_state_dir()
        assert result == tmp_path / "clawdbot"

    def test_blank_openclaw_env_var_ignored(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_STATE_DIR", "   ")
        monkeypatch.delenv("CLAWDBOT_STATE_DIR", raising=False)
        result = resolve_state_dir()
        assert result == Path.home() / ".openclaw"

    def test_blank_clawdbot_env_var_ignored(self, monkeypatch):
        monkeypatch.delenv("OPENCLAW_STATE_DIR", raising=False)
        monkeypatch.setenv("CLAWDBOT_STATE_DIR", "   ")
        result = resolve_state_dir()
        assert result == Path.home() / ".openclaw"

    def test_returns_path_object(self, monkeypatch):
        monkeypatch.delenv("OPENCLAW_STATE_DIR", raising=False)
        monkeypatch.delenv("CLAWDBOT_STATE_DIR", raising=False)
        assert isinstance(resolve_state_dir(), Path)

    def test_env_var_path_not_required_to_exist(self, monkeypatch, tmp_path):
        nonexistent = tmp_path / "does_not_exist"
        monkeypatch.setenv("OPENCLAW_STATE_DIR", str(nonexistent))
        result = resolve_state_dir()
        assert result == nonexistent
        assert not result.exists()
