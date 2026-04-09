"""
Echo Agent 端到端测试入口。

用法：
  python examples/echo_agent/main.py login    # 扫码登录
  python examples/echo_agent/main.py start    # 启动消息循环（Ctrl+C 退出）

测试矩阵（在微信里给 bot 发送以下内容，验证回显正确）：
  1. 文本：随便发一句话              → "你说: <原文>"
  2. 图片：发送一张图片              → 原图回显（验证 AES 密钥编码 Bug 1 修复）
  3. 视频：发送短视频               → 原视频回显
  4. 文件：发送 PDF / Word / ZIP    → 附件回显
  5. 语音：发送语音消息              → 文字摘要（语音无法回显为语音）
  6. 引用消息：长按一条消息回复       → 回显里带 [引用: ...] 前缀
  7. /echo hello                   → 立即回显 + 通道耗时统计
  8. /toggle-debug                 → Debug 模式切换提示
  9. /clear                        → "会话已清除" 提示
  10. Ctrl+C                       → 验证 10 秒内优雅退出
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 允许直接 python examples/echo_agent/main.py 运行（无需 pip install -e .）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from weixin_agent_sdk import login, start  # noqa: E402

# 同时支持 `python main.py` 和 `python -m examples.echo_agent.main` 两种方式
try:
    from echo import EchoAgent  # noqa: E402
except ImportError:
    from examples.echo_agent.echo import EchoAgent  # noqa: E402


async def run_login() -> None:
    """执行扫码登录流程，凭据持久化到 ~/.openclaw/。"""
    print("=" * 60)
    print("Echo Agent —— 扫码登录")
    print("=" * 60)
    account_id = await login()
    print(f"\n✅ 登录成功，账号 ID: {account_id}")
    print("\n下一步: python examples/echo_agent/main.py start")


async def run_start() -> None:
    """启动消息循环，阻塞直到 Ctrl+C 或不可恢复错误。"""
    print("=" * 60)
    print("Echo Agent —— 消息循环")
    print("=" * 60)
    print("按 Ctrl+C 退出（最多等待 10 秒让 in-flight 消息完成）\n")

    agent = EchoAgent()
    await start(agent)


def print_usage() -> None:
    """打印用法说明（来自模块 docstring）。"""
    print(__doc__)


def main() -> None:
    """命令行入口。"""
    if len(sys.argv) != 2 or sys.argv[1] not in ("login", "start"):
        print_usage()
        sys.exit(1)

    cmd = sys.argv[1]
    try:
        if cmd == "login":
            asyncio.run(run_login())
        else:
            asyncio.run(run_start())
    except KeyboardInterrupt:
        # asyncio.run 在 Ctrl+C 时会自动 cancel 所有 task，bot.start 的 finally
        # 块会关闭 cdn_session 和 client，monitor 的 finally 会等 in-flight 消息
        # 最多 10 秒。这里只负责打印退出提示。
        print("\n已退出。")
    except RuntimeError as exc:
        # bot.start() 会在没有账号或账号未配置时抛 RuntimeError
        print(f"\n❌ 启动失败: {exc}", file=sys.stderr)
        print("提示: 先运行 `python examples/echo_agent/main.py login`", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"\n❌ 未预期的错误: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
