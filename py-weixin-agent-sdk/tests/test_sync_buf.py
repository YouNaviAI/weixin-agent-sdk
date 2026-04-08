"""weixin_agent_sdk.storage.sync_buf 的测试用例。"""

import json
import os
from pathlib import Path

import pytest

from weixin_agent_sdk.storage.sync_buf import (
    SyncBufData,
    get_sync_buf_file_path,
    load_get_updates_buf,
    read_sync_buf_file,
    save_get_updates_buf,
)


# ---------------------------------------------------------------------------
# get_sync_buf_file_path
# ---------------------------------------------------------------------------

class TestGetSyncBufFilePath:
    def test_valid_account_id(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path))
        path = get_sync_buf_file_path("user123")
        assert path.name == "user123.sync.json"
        assert "accounts" in str(path)

    def test_account_id_with_at_sign(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path))
        path = get_sync_buf_file_path("user@wx")
        assert path.name == "user@wx.sync.json"

    def test_account_id_with_hyphen_dot(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path))
        path = get_sync_buf_file_path("user-name.v2")
        assert path.name == "user-name.v2.sync.json"

    def test_path_traversal_raises(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path))
        with pytest.raises(ValueError, match="Unsafe"):
            get_sync_buf_file_path("../../etc/passwd")

    def test_slash_in_id_raises(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path))
        with pytest.raises(ValueError, match="Unsafe"):
            get_sync_buf_file_path("user/bad")

    def test_empty_id_raises(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path))
        with pytest.raises(ValueError, match="Unsafe"):
            get_sync_buf_file_path("")

    def test_space_in_id_raises(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path))
        with pytest.raises(ValueError, match="Unsafe"):
            get_sync_buf_file_path("user name")


# ---------------------------------------------------------------------------
# read_sync_buf_file
# ---------------------------------------------------------------------------

class TestReadSyncBufFile:
    def test_returns_none_when_file_missing(self, tmp_path):
        result = read_sync_buf_file(tmp_path / "nonexistent.json")
        assert result is None

    def test_reads_valid_file(self, tmp_path):
        f = tmp_path / "buf.json"
        f.write_text(json.dumps({"get_updates_buf": "buf_value_123"}), encoding="utf-8")
        result = read_sync_buf_file(f)
        assert isinstance(result, SyncBufData)
        assert result.get_updates_buf == "buf_value_123"

    def test_returns_none_when_field_missing(self, tmp_path):
        f = tmp_path / "buf.json"
        f.write_text(json.dumps({"other_key": "value"}), encoding="utf-8")
        result = read_sync_buf_file(f)
        assert result is None

    def test_returns_none_when_field_not_string(self, tmp_path):
        f = tmp_path / "buf.json"
        f.write_text(json.dumps({"get_updates_buf": 12345}), encoding="utf-8")
        result = read_sync_buf_file(f)
        assert result is None

    def test_raises_on_corrupted_json(self, tmp_path):
        f = tmp_path / "buf.json"
        f.write_text("not valid json {{{{", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            read_sync_buf_file(f)

    def test_reads_empty_buf_string(self, tmp_path):
        f = tmp_path / "buf.json"
        f.write_text(json.dumps({"get_updates_buf": ""}), encoding="utf-8")
        result = read_sync_buf_file(f)
        assert result is not None
        assert result.get_updates_buf == ""


# ---------------------------------------------------------------------------
# save_get_updates_buf
# ---------------------------------------------------------------------------

class TestSaveGetUpdatesBuf:
    def test_creates_file(self, tmp_path):
        f = tmp_path / "accounts" / "user.sync.json"
        save_get_updates_buf(f, "buf123")
        assert f.exists()

    def test_creates_parent_dirs(self, tmp_path):
        f = tmp_path / "a" / "b" / "c" / "user.sync.json"
        save_get_updates_buf(f, "buf123")
        assert f.exists()

    def test_written_value_is_correct(self, tmp_path):
        f = tmp_path / "user.sync.json"
        save_get_updates_buf(f, "mytoken_abc")
        data = json.loads(f.read_text(encoding="utf-8"))
        assert data["get_updates_buf"] == "mytoken_abc"

    def test_no_tmp_file_left_on_success(self, tmp_path):
        f = tmp_path / "user.sync.json"
        save_get_updates_buf(f, "buf")
        tmp = f.with_suffix(".tmp")
        assert not tmp.exists()

    def test_atomic_overwrite(self, tmp_path):
        f = tmp_path / "user.sync.json"
        save_get_updates_buf(f, "first")
        save_get_updates_buf(f, "second")
        data = json.loads(f.read_text(encoding="utf-8"))
        assert data["get_updates_buf"] == "second"

    def test_unicode_buf_value(self, tmp_path):
        f = tmp_path / "user.sync.json"
        save_get_updates_buf(f, "token_中文_🔑")
        data = json.loads(f.read_text(encoding="utf-8"))
        assert data["get_updates_buf"] == "token_中文_🔑"


# ---------------------------------------------------------------------------
# load_get_updates_buf
# ---------------------------------------------------------------------------

class TestLoadGetUpdatesBuf:
    def test_loads_primary_path(self, tmp_path):
        f = tmp_path / "user.sync.json"
        save_get_updates_buf(f, "primary_buf")
        result = load_get_updates_buf(f)
        assert result is not None
        assert result.get_updates_buf == "primary_buf"

    def test_returns_none_when_both_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path))
        f = tmp_path / "nonexistent.sync.json"
        result = load_get_updates_buf(f)
        assert result is None

    def test_falls_back_to_legacy_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path))
        legacy = (
            tmp_path
            / "agents"
            / "default"
            / "sessions"
            / ".openclaw-weixin-sync"
            / "default.json"
        )
        legacy.parent.mkdir(parents=True)
        legacy.write_text(json.dumps({"get_updates_buf": "legacy_buf"}), encoding="utf-8")

        primary = tmp_path / "nonexistent.sync.json"
        result = load_get_updates_buf(primary)
        assert result is not None
        assert result.get_updates_buf == "legacy_buf"

    def test_primary_takes_precedence_over_legacy(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path))
        legacy = (
            tmp_path
            / "agents"
            / "default"
            / "sessions"
            / ".openclaw-weixin-sync"
            / "default.json"
        )
        legacy.parent.mkdir(parents=True)
        legacy.write_text(json.dumps({"get_updates_buf": "legacy_buf"}), encoding="utf-8")

        primary = tmp_path / "primary.sync.json"
        save_get_updates_buf(primary, "primary_buf")

        result = load_get_updates_buf(primary)
        assert result is not None
        assert result.get_updates_buf == "primary_buf"

    def test_corrupted_legacy_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path))
        legacy = (
            tmp_path
            / "agents"
            / "default"
            / "sessions"
            / ".openclaw-weixin-sync"
            / "default.json"
        )
        legacy.parent.mkdir(parents=True)
        legacy.write_text("corrupted{{", encoding="utf-8")

        primary = tmp_path / "nonexistent.sync.json"
        result = load_get_updates_buf(primary)
        assert result is None

    def test_corrupted_primary_raises(self, tmp_path):
        f = tmp_path / "user.sync.json"
        f.write_text("not json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            load_get_updates_buf(f)
