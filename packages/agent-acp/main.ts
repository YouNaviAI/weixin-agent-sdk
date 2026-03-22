#!/usr/bin/env node

/**
 * WeChat + ACP (Agent Client Protocol) adapter.
 *
 * Usage:
 *   npx weixin-acp login                          # QR-code login
 *   npx weixin-acp start -- <command> [args...]    # Start bot
 *
 * Examples:
 *   npx weixin-acp start -- codex-acp
 *   npx weixin-acp start -- node ./my-agent.js
 */

import { login, start } from "weixin-agent-sdk";

import { AcpAgent } from "./src/acp-agent.js";

const command = process.argv[2];

async function main() {
  switch (command) {
    case "login": {
      await login();
      break;
    }

    case "start": {
      const ddIndex = process.argv.indexOf("--");
      if (ddIndex === -1 || ddIndex + 1 >= process.argv.length) {
        console.error("错误: 请在 -- 后指定 ACP agent 启动命令");
        console.error("示例: npx weixin-acp start -- codex-acp");
        process.exit(1);
      }

      const [acpCommand, ...acpArgs] = process.argv.slice(ddIndex + 1);

      const agent = new AcpAgent({
        command: acpCommand,
        args: acpArgs,
      });

      // Graceful shutdown
      const ac = new AbortController();
      process.on("SIGINT", () => {
        console.log("\n正在停止...");
        agent.dispose();
        ac.abort();
      });
      process.on("SIGTERM", () => {
        agent.dispose();
        ac.abort();
      });

      await start(agent, { abortSignal: ac.signal });
      break;
    }

    default:
      console.log(`weixin-acp — 微信 + ACP 适配器

用法:
  npx weixin-acp login                          扫码登录微信
  npx weixin-acp start -- <command> [args...]    启动 bot

示例:
  npx weixin-acp start -- codex-acp
  npx weixin-acp start -- node ./my-agent.js`);
      break;
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
