"""解析 OpenClaw 状态目录（默认为 ~/.openclaw/）。"""

import os
from pathlib import Path


def resolve_state_dir() -> Path:
    """
    返回 OpenClaw 状态目录。
    按以下顺序检查：
      1. OPENCLAW_STATE_DIR 环境变量
      2. CLAWDBOT_STATE_DIR 环境变量
      3. ~/.openclaw/
    """
    env_openclaw = os.environ.get("OPENCLAW_STATE_DIR", "").strip()
    if env_openclaw:
        return Path(env_openclaw)

    env_clawdbot = os.environ.get("CLAWDBOT_STATE_DIR", "").strip()
    if env_clawdbot:
        return Path(env_clawdbot)

    return Path.home() / ".openclaw"
