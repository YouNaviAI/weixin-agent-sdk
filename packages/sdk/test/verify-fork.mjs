#!/usr/bin/env node
/**
 * weixin-agent-sdk fork 验证脚本
 *
 * 用法：
 *   # 静态验证（无需微信账号，验证导出和内部逻辑）
 *   node test/verify-fork.mjs
 *
 *   # 监听模式（复用已登录账号，验证收消息 + 空响应 + 独立延迟发送）
 *   node test/verify-fork.mjs --listen
 *
 *   # contextToken 实测（复用已登录账号，测试同一 token 多次发送）
 *   node test/verify-fork.mjs --test-context-token
 *
 *   # 扫码登录（仅在需要新登录时使用）
 *   node test/verify-fork.mjs --login
 */

import {
  // 原有导出
  login,
  start,
  // 新增：消息发送
  sendMessageWeixin,
  sendWeixinMediaFile,
  // 新增：contextToken 管理
  getContextToken,
  setContextToken,
  // 新增：账号解析
  resolveWeixinAccount,
  listWeixinAccountIds,
  // 新增：登录子函数
  startWeixinLoginWithQr,
  waitForWeixinLogin,
} from "../dist/index.mjs";

const args = process.argv.slice(2);
const isLogin = args.includes("--login");
const isListen = args.includes("--listen");
const isTestContextToken = args.includes("--test-context-token");

// ============================================================
// 1. 静态验证：导出检查
// ============================================================

console.log("=== 1. 导出检查 ===\n");

const exports = {
  login,
  start,
  sendMessageWeixin,
  sendWeixinMediaFile,
  getContextToken,
  setContextToken,
  resolveWeixinAccount,
  listWeixinAccountIds,
  startWeixinLoginWithQr,
  waitForWeixinLogin,
};

let allPassed = true;
for (const [name, fn] of Object.entries(exports)) {
  const ok = typeof fn === "function";
  console.log(`  ${ok ? "✅" : "❌"} ${name}: ${typeof fn}`);
  if (!ok) allPassed = false;
}
console.log(allPassed ? "\n✅ 所有导出验证通过" : "\n❌ 部分导出验证失败");

// ============================================================
// 2. contextToken 存取验证
// ============================================================

console.log("\n=== 2. contextToken 存取验证 ===\n");

const testAccountId = "test-account";
const testUserId = "test-user-123";
const testToken = "mock-context-token-abc";

setContextToken(testAccountId, testUserId, testToken);
console.log(`  ✅ setContextToken("${testAccountId}", "${testUserId}", "${testToken}")`);

const retrieved = getContextToken(testAccountId, testUserId);
if (retrieved === testToken) {
  console.log(`  ✅ getContextToken 返回正确: "${retrieved}"`);
} else {
  console.log(`  ❌ getContextToken 返回错误: 期望 "${testToken}", 实际 "${retrieved}"`);
  allPassed = false;
}

const missing = getContextToken(testAccountId, "nonexistent-user");
if (missing === undefined) {
  console.log(`  ✅ getContextToken 不存在的 userId 返回 undefined`);
} else {
  console.log(`  ❌ 应返回 undefined, 实际 "${missing}"`);
  allPassed = false;
}

const newToken = "new-context-token-xyz";
setContextToken(testAccountId, testUserId, newToken);
const overwritten = getContextToken(testAccountId, testUserId);
if (overwritten === newToken) {
  console.log(`  ✅ setContextToken 覆盖成功: "${overwritten}"`);
} else {
  console.log(`  ❌ 覆盖失败: 期望 "${newToken}", 实际 "${overwritten}"`);
  allPassed = false;
}

// ============================================================
// 3. agent.chat() 空响应行为
// ============================================================

console.log("\n=== 3. agent.chat() 空响应行为 ===\n");
console.log("  ℹ️  process-message.ts: else if (response.text) — 空字符串是 falsy");
console.log("  ✅ 确认：agent.chat() 返回 {} 或 { text: '' } 时 SDK 不发送消息");

// ============================================================
// 4. 已登录账号列表
// ============================================================

console.log("\n=== 4. 已登录账号 ===\n");
const accounts = listWeixinAccountIds();
if (accounts.length > 0) {
  console.log(`  ✅ 发现 ${accounts.length} 个已登录账号:`);
  for (const id of accounts) {
    console.log(`     - ${id}`);
    try {
      const resolved = resolveWeixinAccount(id);
      console.log(`       baseUrl: ${resolved.baseUrl}`);
      console.log(`       configured: ${resolved.configured}`);
      console.log(`       token: ${resolved.token ? "***存在***" : "无"}`);
    } catch (err) {
      console.log(`       ⚠️ 解析失败: ${err.message}`);
    }
  }
} else {
  console.log("  ℹ️  没有已登录的账号（使用 --login 进行扫码登录）");
}

// ============================================================
// 静态验证结束
// ============================================================

if (!isLogin && !isListen && !isTestContextToken) {
  console.log("\n" + "=".repeat(50));
  console.log(allPassed ? "✅ 静态验证全部通过" : "❌ 存在验证失败项");
  console.log("=".repeat(50));
  console.log("\n后续手动验证:");
  console.log("  node test/verify-fork.mjs --listen              # 复用已登录账号，测试收发消息");
  console.log("  node test/verify-fork.mjs --test-context-token  # 测试 contextToken 多次使用");
  console.log("  node test/verify-fork.mjs --login               # 扫码登录新账号\n");
  process.exit(allPassed ? 0 : 1);
}

// ============================================================
// 辅助：确保有可用账号
// ============================================================

function ensureAccount() {
  const ids = listWeixinAccountIds();
  if (ids.length === 0) {
    console.log("  ❌ 没有已登录账号，请先运行: node test/verify-fork.mjs --login");
    process.exit(1);
  }
  const account = resolveWeixinAccount(ids[0]);
  if (!account.configured) {
    console.log("  ❌ 账号未配置（缺少 token），请重新登录: node test/verify-fork.mjs --login");
    process.exit(1);
  }
  console.log(`  使用已有账号: ${account.accountId}`);
  console.log(`  baseUrl: ${account.baseUrl}\n`);
  return { ids, account };
}

function setupAbort() {
  const ac = new AbortController();
  process.on("SIGINT", () => {
    console.log("\n  收到 SIGINT，停止...");
    ac.abort();
  });
  return ac;
}

// ============================================================
// 5. --login：扫码登录
// ============================================================

if (isLogin) {
  console.log("\n=== 5. 扫码登录 ===\n");
  try {
    const accountId = await login({
      log: (msg) => console.log(`  [SDK] ${msg}`),
    });
    console.log(`\n  ✅ 登录成功，accountId: ${accountId}`);
    console.log("  现在可以运行: node test/verify-fork.mjs --listen");
  } catch (err) {
    console.log(`  ❌ 登录失败: ${err.message}`);
    process.exit(1);
  }
}

// ============================================================
// 6. --listen：复用已有账号，测试收消息 + 空响应 + 独立发送
// ============================================================

if (isListen) {
  console.log("\n=== 6. 监听模式：收消息 + 空响应 + 独立延迟发送 ===\n");

  const { ids, account } = ensureAccount();
  const ac = setupAbort();

  console.log("  验证内容:");
  console.log("    1. 收到消息后 agent.chat() 返回空 → 微信不收到自动回复");
  console.log("    2. 3 秒后通过 sendMessageWeixin 独立发送延迟回复");
  console.log("  请用微信给 bot 发一条消息，按 Ctrl+C 退出\n");

  const testAgent = {
    async chat(request) {
      console.log(`\n  📩 收到消息:`);
      console.log(`     from: ${request.conversationId}`);
      console.log(`     text: ${request.text}`);
      if (request.media) {
        console.log(`     media: ${request.media.type} → ${request.media.filePath}`);
      }

      const ct = getContextToken(account.accountId, request.conversationId);
      console.log(`     contextToken: ${ct ? ct.substring(0, 30) + "..." : "❌ 缺失"}`);

      console.log("  ⏳ 返回空响应（验证 SDK 不发送消息）...");

      // 3 秒后独立发送回复
      if (ct) {
        setTimeout(async () => {
          console.log("  📤 延迟 3 秒，独立发送回复...");
          try {
            const result = await sendMessageWeixin({
              to: request.conversationId,
              text: `[YouNavi fork 验证] 收到: "${request.text}" (独立延迟发送)`,
              opts: {
                baseUrl: account.baseUrl,
                token: account.token,
                contextToken: ct,
              },
            });
            console.log(`  ✅ 独立发送成功，messageId: ${result.messageId}`);
          } catch (err) {
            console.log(`  ❌ 独立发送失败: ${err.message}`);
          }
        }, 3000);
      }

      return {};  // 空响应，SDK 不发送
    },
  };

  try {
    await start(testAgent, {
      accountId: ids[0],
      abortSignal: ac.signal,
      log: (msg) => console.log(`  [SDK] ${msg}`),
    });
  } catch (err) {
    if (err.name !== "AbortError") {
      console.log(`  ❌ 监听异常: ${err.message}`);
    }
  }
  console.log("  监听模式结束");
}

// ============================================================
// 7. --test-context-token：多次使用同一 contextToken
// ============================================================

if (isTestContextToken) {
  console.log("\n=== 7. contextToken 多次使用实测 ===\n");

  const { ids, account } = ensureAccount();
  const ac = setupAbort();

  console.log("  验证内容: 用同一个 contextToken 连续发送 5 条消息");
  console.log("  请用微信给 bot 发一条消息，按 Ctrl+C 退出\n");

  let testDone = false;

  const testAgent = {
    async chat(request) {
      if (testDone) return {};

      const ct = getContextToken(account.accountId, request.conversationId);
      console.log(`\n  📩 收到消息: "${request.text}"`);
      console.log(`  🔑 contextToken: ${ct ? ct.substring(0, 30) + "..." : "缺失"}`);

      if (!ct) {
        console.log("  ❌ contextToken 缺失，无法测试");
        return {};
      }

      testDone = true;

      setTimeout(async () => {
        for (let i = 1; i <= 5; i++) {
          try {
            const result = await sendMessageWeixin({
              to: request.conversationId,
              text: `[contextToken 测试] 第 ${i}/5 条消息`,
              opts: {
                baseUrl: account.baseUrl,
                token: account.token,
                contextToken: ct,
              },
            });
            console.log(`  ✅ 第 ${i}/5 条发送成功: ${result.messageId}`);
          } catch (err) {
            console.log(`  ❌ 第 ${i}/5 条发送失败: ${err.message}`);
            console.log(`  ℹ️  contextToken 使用上限可能是 ${i - 1} 次`);
            break;
          }
          await new Promise((r) => setTimeout(r, 1000));
        }
        console.log("\n  ✅ contextToken 多次使用测试完成");
        console.log("  按 Ctrl+C 退出");
      }, 1000);

      return {};
    },
  };

  try {
    await start(testAgent, {
      accountId: ids[0],
      abortSignal: ac.signal,
      log: (msg) => console.log(`  [SDK] ${msg}`),
    });
  } catch (err) {
    if (err.name !== "AbortError") {
      console.log(`  ❌ 异常: ${err.message}`);
    }
  }
}
