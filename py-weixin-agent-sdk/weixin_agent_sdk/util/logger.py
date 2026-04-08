"""
插件日志记录器 — 将 JSON 行写入主 openclaw 日志文件：
  /tmp/openclaw/openclaw-YYYY-MM-DD.log  （Unix）
  %TEMP%/openclaw/openclaw-YYYY-MM-DD.log  （Windows）

与所有其他 openclaw 通道使用相同的文件和格式。
"""

import atexit
import json
import os
import socket
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from typing import TextIO

MAIN_LOG_DIR = Path(tempfile.gettempdir()) / "openclaw"
SUBSYSTEM = "gateway/channels/openclaw-weixin"
RUNTIME = "python"
RUNTIME_VERSION = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
PARENT_NAMES: tuple[str, ...] = ("openclaw",)

try:
    HOSTNAME = socket.gethostname()
except Exception:
    HOSTNAME = "unknown"


class LogLevel(IntEnum):
    TRACE = 1
    DEBUG = 2
    INFO = 3
    WARN = 4
    ERROR = 5
    FATAL = 6

    @classmethod
    def from_str(cls, name: str) -> "LogLevel":
        try:
            return cls[name.upper()]
        except KeyError:
            valid = ", ".join(m.name for m in cls)
            raise ValueError(f"无效的日志级别: {name!r}。合法级别: {valid}")


@dataclass
class LogMeta:
    runtime: str
    runtimeVersion: str
    hostname: str
    name: str
    parentNames: list[str]
    date: str
    logLevelId: int
    logLevelName: str


@dataclass
class LogEntry:
    logger_name: str
    message: str
    meta: LogMeta
    time: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "0": self.logger_name,
                "1": self.message,
                "_meta": asdict(self.meta),
                "time": self.time,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )


@dataclass
class LoggerState:
    min_level: LogLevel
    log_dir_ensured: bool
    current_log_path: Path | None
    file_handle: TextIO | None


STATE = LoggerState(
    min_level=LogLevel.INFO,
    log_dir_ensured=False,
    current_log_path=None,
    file_handle=None,
)


def resolve_env_log_level() -> LogLevel:
    env = os.environ.get("OPENCLAW_LOG_LEVEL", "")
    if env:
        try:
            return LogLevel.from_str(env)
        except ValueError:
            pass
    return LogLevel.INFO


def set_log_level(level: str) -> None:
    """运行时动态修改最低日志级别。"""
    STATE.min_level = LogLevel.from_str(level)


def to_local_iso(now: datetime) -> str:
    return now.astimezone().isoformat(timespec="milliseconds")


def get_log_path_for(now: datetime) -> Path:
    date_key = now.astimezone().date().isoformat()
    return MAIN_LOG_DIR / f"openclaw-{date_key}.log"


def acquire_file_handle(log_path: Path) -> TextIO | None:
    """打开（或复用）日志文件句柄，日期变更时自动轮转。"""
    if STATE.current_log_path == log_path and STATE.file_handle is not None:
        return STATE.file_handle

    if STATE.file_handle is not None:
        try:
            STATE.file_handle.close()
        except Exception:
            pass
        STATE.file_handle = None

    try:
        if not STATE.log_dir_ensured:
            MAIN_LOG_DIR.mkdir(parents=True, exist_ok=True)
            STATE.log_dir_ensured = True
        handle = log_path.open("a", encoding="utf-8", buffering=1)
        STATE.current_log_path = log_path
        STATE.file_handle = handle
        return handle
    except Exception:
        return None


def write_log(level: LogLevel, message: str, account_id: str | None = None) -> None:
    if level < STATE.min_level:
        return

    # 捕获单次时间戳，同时用于日志路径（日期键）和日志条目内容。
    now = datetime.now(timezone.utc)
    log_path = get_log_path_for(now)

    logger_name = f"{SUBSYSTEM}/{account_id}" if account_id else SUBSYSTEM
    prefixed_message = f"[{account_id}] {message}" if account_id else message

    entry = LogEntry(
        logger_name=logger_name,
        message=prefixed_message,
        meta=LogMeta(
            runtime=RUNTIME,
            runtimeVersion=RUNTIME_VERSION,
            hostname=HOSTNAME,
            name=logger_name,
            parentNames=PARENT_NAMES,
            date=now.isoformat(),
            logLevelId=int(level),
            logLevelName=level.name,
        ),
        time=to_local_iso(now),
    )

    try:
        handle = acquire_file_handle(log_path)
        if handle is not None:
            handle.write(entry.to_json() + "\n")
    except Exception:
        pass  # 尽力而为；日志写入失败不应阻塞主流程。


class Logger:
    """日志记录器实例，可选择绑定到特定账号。"""

    def __init__(self, account_id: str | None = None) -> None:
        self.account_id = account_id

    def info(self, message: str) -> None:
        write_log(LogLevel.INFO, message, self.account_id)

    def debug(self, message: str) -> None:
        write_log(LogLevel.DEBUG, message, self.account_id)

    def warn(self, message: str) -> None:
        write_log(LogLevel.WARN, message, self.account_id)

    def error(self, message: str) -> None:
        write_log(LogLevel.ERROR, message, self.account_id)

    def with_account(self, account_id: str) -> "Logger":
        """返回一个绑定了 account_id 的子日志记录器，消息会带 [account_id] 前缀。"""
        return Logger(account_id)

    def get_log_file_path(self) -> Path:
        """返回当前主日志文件路径。"""
        return get_log_path_for(datetime.now(timezone.utc))


# 模块级默认日志记录器（未绑定账号）。
logger = Logger()

# 模块导入时从环境变量读取日志级别。
STATE.min_level = resolve_env_log_level()

# 进程退出时确保文件句柄被正确关闭。
atexit.register(lambda: STATE.file_handle.close() if STATE.file_handle else None)
