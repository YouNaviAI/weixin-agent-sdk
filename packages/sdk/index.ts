export type { Agent, ChatRequest, ChatResponse } from "./src/agent/interface.js";
export { login, start } from "./src/bot.js";
export type { LoginOptions, StartOptions } from "./src/bot.js";

// --- YouNavi fork: 新增导出（用于独立发送消息和 contextToken 管理） ---

// 消息发送
export { sendMessageWeixin } from "./src/messaging/send.js";
export { sendWeixinMediaFile } from "./src/messaging/send-media.js";

// contextToken 管理
export { getContextToken, setContextToken } from "./src/messaging/inbound.js";

// 账号解析与管理
export {
  resolveWeixinAccount,
  listWeixinAccountIds,
  normalizeAccountId,
  saveWeixinAccount,
  registerWeixinAccountId,
} from "./src/auth/accounts.js";
export type { ResolvedWeixinAccount } from "./src/auth/accounts.js";

// API 类型
export type { WeixinApiOptions } from "./src/api/api.js";

// 登录子函数（用于 Worker Thread 中分步控制登录流程）
export { startWeixinLoginWithQr, waitForWeixinLogin } from "./src/auth/login-qr.js";
export type { WeixinQrStartResult, WeixinQrWaitResult } from "./src/auth/login-qr.js";
