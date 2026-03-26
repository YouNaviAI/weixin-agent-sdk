# wechat_bridge

微信个人号消息桥接模块，基于 [weixin-agent-sdk](https://www.npmjs.com/package/weixin-agent-sdk) 实现。通过腾讯 iLink AI Agent 平台的长轮询机制收发微信消息。

## 工作原理

`weixin-agent-sdk` 通过 iLink 平台（`ilinkai.weixin.qq.com`）与微信通信：

1. **登录**：调用 iLink API 获取二维码，用户使用微信扫码关联 bot
2. **收消息**：SDK 以长轮询方式调用 `getupdates` 接收新消息
3. **发消息**：SDK 调用 `sendmessage` 将回复发送到微信

bot 在微信中表现为一个联系人，其他微信用户可以直接给它发消息。

## 用法

从 `nexduit/wechat_bridge` 目录执行：

```bash
# 安装依赖
pnpm install

# 第一步：扫码登录（终端显示二维码，用微信扫描）
pnpm run login

# 第二步：启动 bot
pnpm run start
```

登录凭证保存在 `~/.openclaw/accounts/` 下，后续启动无需重新扫码（除非登录态过期）。

## 当前功能

Echo Bot 演示：对所有收到的消息，回复当前时间 + 当前消息内容 + 上一条消息内容。

## 二维码获取机制

`login()` 的二维码来源于 iLink API 返回的 `qrcode_img_content` 字段，这是一个 **HTTPS URL**（指向腾讯 CDN 上的二维码图片）。

SDK 内部的处理流程：

1. `GET ilink/bot/get_bot_qrcode?bot_type=3` → 返回 `{ qrcode, qrcode_img_content }`
2. `qrcode_img_content` 是二维码图片的 URL
3. SDK 用 `qrcode-terminal` 库将该 URL 编码为终端字符二维码显示
4. 同时通过 `startWeixinLoginWithQr()` 返回 `qrcodeUrl` 字段

### 在其它页面中展示二维码

SDK 的 `login()` 封装了完整流程（生成 + 终端显示 + 等待扫码），不方便拆分。若需要在 Web 页面或 Electron 窗口中展示二维码，应直接使用 SDK 导出的底层函数：

```typescript
import {
  startWeixinLoginWithQr,
  waitForWeixinLogin,
} from "weixin-agent-sdk/dist/auth/login-qr.mjs";

// 1. 获取二维码 URL
const result = await startWeixinLoginWithQr({
  apiBaseUrl: "https://ilinkai.weixin.qq.com",
  botType: "3",
});

// result.qrcodeUrl 就是二维码图片的 HTTPS URL
// 可以直接作为 <img src="..."> 展示，也可以用 qrcode 库渲染

// 2. 等待用户扫码确认
const loginResult = await waitForWeixinLogin({
  sessionKey: result.sessionKey,
  apiBaseUrl: "https://ilinkai.weixin.qq.com",
  botType: "3",
  timeoutMs: 480_000,
});

if (loginResult.connected) {
  // 登录成功，保存 loginResult.botToken / accountId / baseUrl
}
```

**关键字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `qrcodeUrl` | `string` | 二维码图片的 HTTPS URL（腾讯 CDN），可直接用于 `<img>` 标签 |
| `sessionKey` | `string` | 本次登录会话标识，传给 `waitForWeixinLogin` 轮询状态 |
| `qrcode` | `string` | 二维码原始值（内部用于状态轮询，外部一般不需要） |

**注意**：`startWeixinLoginWithQr` 和 `waitForWeixinLogin` 目前不是 SDK 的公开导出，需要从 `dist/auth/login-qr.mjs` 直接引用。后续正式集成时建议 fork SDK 将这两个函数加入公开导出。
