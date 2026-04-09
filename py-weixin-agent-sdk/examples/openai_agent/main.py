"""
OpenAI Agent 示例入口。

使用方式：
  1. 首次登录：
       python -m examples.openai_agent.main login

  2. 启动 bot：
       OPENAI_API_KEY=sk-... python -m examples.openai_agent.main start

  3. 指定账号和模型：
       OPENAI_API_KEY=sk-... OPENAI_MODEL=gpt-4o python -m examples.openai_agent.main start --account myid

环境变量：
  OPENAI_API_KEY   （必填）OpenAI API Key
  OPENAI_BASE_URL  （可选）自定义 API 地址
  OPENAI_MODEL     （可选）模型名称，默认 gpt-4o
  SYSTEM_PROMPT    （可选）系统提示词
"""

from __future__ import annotations

import asyncio
import os
import sys


def main() -> None:
    args = sys.argv[1:]
    cmd = args[0] if args else "start"

    if cmd == "login":
        asyncio.run(do_login())
    elif cmd == "start":
        account_id = None
        if "--account" in args:
            idx = args.index("--account")
            if idx + 1 < len(args):
                account_id = args[idx + 1]
        asyncio.run(do_start(account_id))
    else:
        print(f"未知命令: {cmd}。支持: login | start [--account ID]")
        sys.exit(1)


async def do_login() -> None:
    from weixin_agent_sdk import LoginOptions, login
    account_id = await login(LoginOptions(log=print))
    print(f"登录成功，account_id={account_id}")


async def do_start(account_id: str | None) -> None:
    from weixin_agent_sdk import StartOptions, start

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("错误：请设置 OPENAI_API_KEY 环境变量")
        sys.exit(1)

    from examples.openai_agent.agent import OpenAIAgent, OpenAIAgentOptions

    agent = OpenAIAgent(OpenAIAgentOptions(
        api_key=api_key,
        model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
        base_url=os.environ.get("OPENAI_BASE_URL"),
        system_prompt=os.environ.get("SYSTEM_PROMPT"),
    ))

    print(f"启动 bot，按 Ctrl+C 停止...")
    stop_event = asyncio.Event()

    loop = asyncio.get_event_loop()

    def handle_sigint():
        print("\n收到停止信号，正在优雅退出...")
        stop_event.set()

    try:
        loop.add_signal_handler(__import__("signal").SIGINT, handle_sigint)
    except (NotImplementedError, OSError):
        pass  # Windows 下 add_signal_handler 不可用

    await start(agent, StartOptions(account_id=account_id, stop_event=stop_event, log=print))


if __name__ == "__main__":
    main()
