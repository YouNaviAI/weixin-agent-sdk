"""weixin_agent_sdk.util.logger 的测试用例。"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# 必须先触发导入，再通过 sys.modules 获取真实模块对象。
import weixin_agent_sdk.util.logger  # 确保注册到 sys.modules
_mod = sys.modules["weixin_agent_sdk.util.logger"]

from weixin_agent_sdk.util.logger import (
    LogLevel,
    Logger,
    STATE,
    get_log_path_for,
    set_log_level,
    to_local_iso,
    write_log,
)


# ---------------------------------------------------------------------------
# Fixture：为每个测试重定向 MAIN_LOG_DIR 并重置 STATE
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_log_dir(tmp_path, monkeypatch):
    log_dir = tmp_path / "openclaw"
    monkeypatch.setattr(_mod, "MAIN_LOG_DIR", log_dir)

    # 关闭上一个测试遗留的文件句柄。
    if STATE.file_handle is not None:
        try:
            STATE.file_handle.close()
        except Exception:
            pass
    STATE.file_handle = None
    STATE.current_log_path = None
    STATE.log_dir_ensured = False
    STATE.min_level = LogLevel.INFO

    yield

    if STATE.file_handle is not None:
        try:
            STATE.file_handle.close()
        except Exception:
            pass
    STATE.file_handle = None
    STATE.current_log_path = None


# ---------------------------------------------------------------------------
# LogLevel
# ---------------------------------------------------------------------------

class TestLogLevel:
    def test_ordering(self):
        assert LogLevel.TRACE < LogLevel.DEBUG < LogLevel.INFO
        assert LogLevel.INFO < LogLevel.WARN < LogLevel.ERROR < LogLevel.FATAL

    def test_from_str_valid(self):
        assert LogLevel.from_str("info") == LogLevel.INFO
        assert LogLevel.from_str("DEBUG") == LogLevel.DEBUG
        assert LogLevel.from_str("WARN") == LogLevel.WARN

    def test_from_str_case_insensitive(self):
        assert LogLevel.from_str("Error") == LogLevel.ERROR

    def test_from_str_invalid_raises(self):
        with pytest.raises(ValueError, match="无效的日志级别"):
            LogLevel.from_str("verbose")

    def test_int_values(self):
        assert int(LogLevel.TRACE) == 1
        assert int(LogLevel.FATAL) == 6


# ---------------------------------------------------------------------------
# to_local_iso / get_log_path_for
# ---------------------------------------------------------------------------

class TestDateHelpers:
    def test_to_local_iso_returns_string(self):
        now = datetime.now(timezone.utc)
        result = to_local_iso(now)
        assert isinstance(result, str)
        assert len(result) >= 10

    def test_get_log_path_for_uses_local_date(self):
        now = datetime.now(timezone.utc)
        path = get_log_path_for(now)
        local_date = now.astimezone().date().isoformat()
        assert local_date in path.name

    def test_get_log_path_for_different_days_different_files(self):
        now = datetime.now(timezone.utc)
        yesterday = now - timedelta(days=1)
        assert get_log_path_for(now) != get_log_path_for(yesterday)


# ---------------------------------------------------------------------------
# write_log / 文件输出
# ---------------------------------------------------------------------------

class TestWriteLog:
    def _read_log_lines(self, tmp_path) -> list[dict]:
        log_dir = tmp_path / "openclaw"
        lines = []
        if log_dir.exists():
            for f in log_dir.glob("*.log"):
                for line in f.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        lines.append(json.loads(line))
        return lines

    def test_creates_log_file(self, tmp_path):
        write_log(LogLevel.INFO, "hello")
        log_dir = tmp_path / "openclaw"
        assert any(log_dir.glob("*.log"))

    def test_log_entry_structure(self, tmp_path):
        write_log(LogLevel.INFO, "test message")
        lines = self._read_log_lines(tmp_path)
        assert len(lines) == 1
        entry = lines[0]
        assert "0" in entry       # logger_name
        assert "1" in entry       # message
        assert "_meta" in entry
        assert "time" in entry

    def test_message_in_output(self, tmp_path):
        write_log(LogLevel.INFO, "specific message content")
        lines = self._read_log_lines(tmp_path)
        assert lines[0]["1"] == "specific message content"

    def test_account_id_prefixes_message(self, tmp_path):
        write_log(LogLevel.INFO, "hello", account_id="wx123")
        lines = self._read_log_lines(tmp_path)
        assert "[wx123]" in lines[0]["1"]
        assert "wx123" in lines[0]["0"]

    def test_log_level_in_meta(self, tmp_path):
        write_log(LogLevel.WARN, "warning!")
        lines = self._read_log_lines(tmp_path)
        meta = lines[0]["_meta"]
        assert meta["logLevelName"] == "WARN"
        assert meta["logLevelId"] == int(LogLevel.WARN)

    def test_below_min_level_not_written(self, tmp_path):
        STATE.min_level = LogLevel.ERROR
        write_log(LogLevel.DEBUG, "should be suppressed")
        log_dir = tmp_path / "openclaw"
        total = 0
        if log_dir.exists():
            total = sum(
                len([l for l in f.read_text().splitlines() if l.strip()])
                for f in log_dir.glob("*.log")
            )
        assert total == 0

    def test_multiple_entries_in_same_file(self, tmp_path):
        write_log(LogLevel.INFO, "first")
        write_log(LogLevel.INFO, "second")
        write_log(LogLevel.INFO, "third")
        lines = self._read_log_lines(tmp_path)
        assert len(lines) == 3

    def test_timestamps_consistent(self, tmp_path):
        write_log(LogLevel.INFO, "ts check")
        lines = self._read_log_lines(tmp_path)
        meta = lines[0]["_meta"]
        time_field = lines[0]["time"]
        # 两个时间戳必须引用相同的日期。
        assert meta["date"][:10] == time_field[:10]

    def test_runtime_field(self, tmp_path):
        write_log(LogLevel.INFO, "runtime check")
        lines = self._read_log_lines(tmp_path)
        assert lines[0]["_meta"]["runtime"] == "python"

    def test_valid_json_output(self, tmp_path):
        write_log(LogLevel.INFO, "json check")
        log_dir = tmp_path / "openclaw"
        for f in log_dir.glob("*.log"):
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    json.loads(line)  # 不得抛出异常

    def test_unicode_message(self, tmp_path):
        write_log(LogLevel.INFO, "消息内容 🎉")
        lines = self._read_log_lines(tmp_path)
        assert "消息内容 🎉" in lines[0]["1"]

    def test_file_handle_reused(self, tmp_path):
        write_log(LogLevel.INFO, "first")
        handle_after_first = STATE.file_handle
        write_log(LogLevel.INFO, "second")
        handle_after_second = STATE.file_handle
        assert handle_after_first is handle_after_second

    def test_no_exception_on_log_dir_creation(self, tmp_path):
        # 目录尚不存在 — write_log 应静默创建它。
        assert not (tmp_path / "openclaw").exists()
        write_log(LogLevel.INFO, "trigger creation")
        assert (tmp_path / "openclaw").exists()


# ---------------------------------------------------------------------------
# set_log_level
# ---------------------------------------------------------------------------

class TestSetLogLevel:
    def test_raises_on_invalid(self):
        with pytest.raises(ValueError):
            set_log_level("verbose")

    def test_changes_min_level(self):
        set_log_level("DEBUG")
        assert STATE.min_level == LogLevel.DEBUG

    def test_suppresses_below_new_level(self, tmp_path):
        set_log_level("ERROR")
        write_log(LogLevel.WARN, "suppressed")
        log_dir = tmp_path / "openclaw"
        total = 0
        if log_dir.exists():
            total = sum(
                len([l for l in f.read_text().splitlines() if l.strip()])
                for f in log_dir.glob("*.log")
            )
        assert total == 0


# ---------------------------------------------------------------------------
# Logger 类
# ---------------------------------------------------------------------------

class TestLoggerClass:
    def test_info_writes(self, tmp_path):
        log = Logger()
        log.info("info message")
        log_dir = tmp_path / "openclaw"
        assert any(log_dir.glob("*.log"))

    def test_with_account_binds_id(self, tmp_path):
        log = Logger().with_account("acc_99")
        log.warn("bound message")
        log_dir = tmp_path / "openclaw"
        content = "".join(f.read_text() for f in log_dir.glob("*.log"))
        assert "acc_99" in content

    def test_get_log_file_path_returns_path(self):
        log = Logger()
        p = log.get_log_file_path()
        assert isinstance(p, Path)
        assert p.suffix == ".log"

    def test_debug_suppressed_at_info_level(self, tmp_path):
        STATE.min_level = LogLevel.INFO
        Logger().debug("should not appear")
        log_dir = tmp_path / "openclaw"
        total = 0
        if log_dir.exists():
            total = sum(
                len([l for l in f.read_text().splitlines() if l.strip()])
                for f in log_dir.glob("*.log")
            )
        assert total == 0

    def test_error_written_at_info_level(self, tmp_path):
        Logger().error("error always visible")
        lines = []
        log_dir = tmp_path / "openclaw"
        if log_dir.exists():
            for f in log_dir.glob("*.log"):
                for line in f.read_text().splitlines():
                    if line.strip():
                        lines.append(json.loads(line))
        assert any("error always visible" in e["1"] for e in lines)
