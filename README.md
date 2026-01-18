# 🍔 McDonald's Auto Coupon Claimer (MCP)

全自动麦当劳优惠券领取工具，基于 MCP (Model Context Protocol) 协议。支持个人独享模式和 Telegram Bot 共享模式。

## ✨ 功能特点

- **全自动领取**：自动调用麦当劳 MCP 工具领取所有可用优惠券 (`auto-bind-coupons`)。
- **多模式支持**：
    - 🏠 **个人独享**：通过 GitHub Actions 或本地脚本运行，适合个人使用。
    - 🤖 **Bot 共享**：搭建 Telegram Bot，朋友只需发送 Token 即可使用，适合分享给小白朋友。
- **多渠道推送**：支持 Telegram, Bark, 飞书, Server酱等多种推送方式。
- **零成本部署**：支持 GitHub Actions 免费运行。

---

## 🚀 快速开始

### 第一步：获取 MCP Token

1. 访问 [麦当劳 MCP 控制台](https://open.mcd.cn/mcp/console)。
2. 登录并点击“申请 Token”。
3. 复制获得的 Token（请妥善保管）。

---

## 🏠 场景一：个人独享 (GitHub Actions)

不需要服务器，不需要懂代码，GitHub 帮你每天自动跑。

1. **Fork 本仓库** 到你的 GitHub 账号。
2. 进入仓库的 **Settings** -> **Secrets and variables** -> **Actions**。
3. 点击 **New repository secret**，添加以下变量：
    - `MCD_MCP_TOKEN`: 你的麦当劳 Token (必填)
    - `TG_BOT_TOKEN`: (可选) Telegram Bot Token，用于接收通知
    - `TG_CHAT_ID`: (可选) Telegram Chat ID，用于接收通知
    - *(其他推送配置见下文)*
4. 搞定！程序会在每天 **北京时间 10:30** 自动运行。
   - 你也可以在 **Actions** 页面手动点击 **Run workflow** 立即测试。

---

## 🤖 场景二：Bot 共享 (Docker)

如果你有服务器 (VPS, NAS, 树莓派)，可以运行一个 Telegram Bot，你和朋友都能用。

### 1. 准备工作
- 申请一个 Telegram Bot Token (找 @BotFather)。
- 确保服务器安装了 Docker。

### 2. 配置
修改 `.env` 文件 (复制 `.env.example` 重命名)：
```ini
# 必须配置
TG_BOT_TOKEN=你的_Telegram_Bot_Token

# 可选：如果你自己也想通过环境变量配置(不走Bot对话)，可以填这个
MCD_MCP_TOKEN=你的麦当劳Token
```

### 3. 启动
```bash
docker-compose up -d
```

### 4. 使用方法
1. 在 Telegram 上找到你的 Bot。
2. 发送 `/start`。
3. 直接发送 **MCP Token** 给 Bot。
4. Bot 会验证 Token，如果正确，就会自动保存。
5. 之后你可以使用以下命令：
   - `/claim`：立即领券
   - `/coupons`：查看当前可领优惠券
   - `/mycoupons`：查看你已拥有的优惠券
   - `/calendar`：查看活动日历（可选参数 YYYY-MM-DD）
   - `/today`：基于活动日历与当前可领券，生成「今日用券建议」
   - `/status`：查看当前绑定状态和自动领券开关
   - `/stats`：查看自己的领券统计
   - `/autoclaim on` / `/autoclaim off`：开启或关闭每日自动领券
   - `/account add/use/list/del`：多账号管理（支持为同一个人绑定多个麦当劳账号）
   - `/admin`：管理员总览（仅 TG_CHAT_ID 对应账号可用，支持 `/admin sweep` 立即执行一次全量自动领券）
6. **以后每天 10:30，Bot 会自动帮所有开启自动领券的用户领券，并私聊通知结果。**

---

## ☁️ 场景三：免费 PaaS 部署 (Koyeb / HF Spaces)

如果你没有服务器，可以使用免费的 PaaS 平台部署 Bot。
**注意：** 免费实例重启后可能会丢失数据库（丢失已绑定的朋友），建议使用外挂数据库或定期备份。

### 🚀 Hugging Face Spaces

1. 创建一个新的 **Space**，SDK 选择 **Docker**。
2. 将本仓库代码上传（或 Clone）。
3. 在 Space 的 **Settings** -> **Variables and secrets** 中添加 Secret：
   - `TG_BOT_TOKEN`: 你的 Telegram Bot Token
4. 部署完成后，Bot 即可在线。

### 🚀 Koyeb

1. 注册 Koyeb 并创建新 App。
2. 选择 **GitHub** 部署，连接你的仓库。
3. 在 **Environment Variables** 中添加：
   - `TG_BOT_TOKEN`: 你的 Telegram Bot Token
   - `PORT`: 8000 (可选)
4. 部署即可。

### 🚀 Zeabur / Northflank (解决数据丢失问题)
这些平台也支持 Docker，但默认情况下重启会丢失数据。你需要挂载一个 Volume (存储卷) 来保存数据库。

1. **创建服务**：使用 Docker 镜像或 GitHub 仓库部署。
2. **环境变量**：设置 `TG_BOT_TOKEN`。
3. **挂载 Volume**：
   - 创建一个 Persistent Volume (持久化存储)。
   - 将其挂载到容器内的 `/app/data` 目录。
   - **关键一步**：添加环境变量 `DB_PATH=/app/data/users.db`。
   - 这样 Bot 就会把数据库保存在挂载的卷里，重启也不会丢了。

---

## 🛠️ 高级配置

### 环境变量说明 (.env 或 GitHub Secrets)

| 变量名 | 必填 | 说明 |
| :--- | :--- | :--- |
| `TG_BOT_TOKEN` | ✅ | Telegram Bot Token (Bot 模式必填) |
| `MCD_MCP_TOKEN` | ❌ | 麦当劳 Token (Actions 模式必填；Bot 模式选填，填了会自动绑定给 Owner) |
| `TG_CHAT_ID` | ❌ | Owner 的 Telegram Chat ID (用于自动绑定 Owner Token) |
| `DB_PATH` | ❌ | 数据库路径，默认 `users.db`。Zeabur/Northflank 请设为 `/app/data/users.db` |
| `TZ` | ❌ | 时区设置，默认 `Asia/Shanghai`。Docker 部署建议检查此项。 |
| `BARK_URL` | ❌ | Bark 推送链接 (Actions 模式用) |
| `FEISHU_WEBHOOK` | ❌ | 飞书 Webhook (Actions 模式用) |
| `SERVERCHAN_KEY` | ❌ | Server酱 Key (Actions 模式用) |

### ⚠️ 注意事项

1. **Token 过期**：麦当劳 Token 可能会过期。如果过期，Bot 会在每天的推送中提示你 `Token invalid`，此时请重新发送新 Token 给 Bot 即可。
2. **时区问题**：程序默认设定每天 10:30 运行。Docker 镜像已内置 `Asia/Shanghai` 时区，确保你领券是在白天而不是半夜。
3. **隐私安全**：Bot 的数据库存储了用户的 Token。请确保你的数据库文件（`users.db`）安全，不要分享给他人。

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

MIT License
