#!/usr/bin/env node

/**
 * 微信 Echo Bot — 回复当前时间 + 上一条消息内容。
 *
 * 用法:
 *   pnpm run login    扫码登录微信（终端显示二维码）
 *   pnpm run start    启动 bot，开始接收和回复消息
 */

import * as sdk from "weixin-agent-sdk";

/** 每个用户的上一条消息记录 */
export const lastMessages = new Map<string, string>();

export const echoAgent: sdk.Agent = {
  async chat(request: sdk.ChatRequest): Promise<sdk.ChatResponse> {
    const now = new Date().toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" });
    const lastMsg = lastMessages.get(request.conversationId);
    const currentMsg = request.text || "[非文本消息]";

    let reply = `当前时间: ${now}\n你说的: ${currentMsg}`;
    if (lastMsg) {
      reply += `\n上一条: ${lastMsg}`;
    }

    lastMessages.set(request.conversationId, currentMsg);
    return { text: reply };
  },
};

export const command = process.argv[2];

export async function handleLogin() {
  await sdk.login();
}

export const ac = new AbortController();

export function onSigint() {
  console.log("\n正在停止...");
  ac.abort();
}

export function onSigterm() {
  ac.abort();
}

export async function handleStart() {
  process.on("SIGINT", onSigint);
  process.on("SIGTERM", onSigterm);

  console.log("Echo Bot 已启动，等待微信消息...");
  await sdk.start(echoAgent, { abortSignal: ac.signal });
}

export function printUsage() {
  console.log(`微信 Echo Bot

用法:
  pnpm run login    扫码登录微信
  pnpm run start    启动 bot`);
}

export async function main() {
  switch (command) {
    case "login":
      await handleLogin();
      break;
    case "start":
      await handleStart();
      break;
    default:
      printUsage();
      break;
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
