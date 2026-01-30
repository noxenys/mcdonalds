# 🍔 McDonald's Auto Coupon Claimer (MCP)

全自动麦当劳优惠券领取工具，基于 MCP (Model Context Protocol) 协议。支持个人独享模式和 Telegram Bot 共享模式。

## ✨ 功能特点

### 🤖 智能自动化
- **全自动领取**：每天10:30自动调用麦当劳 MCP 工具领取所有可用优惠券
- **智能提醒**：
  - 🍔 **午餐提醒**（11:30）：准备吃午饭时提醒你有什么券可用
  - 🍗 **晚餐提醒**（17:30）：准备吃晚饭时提醒你有什么券可用
  - ⏰ **过期提醒**（20:00）：提前3天提醒即将过期的优惠券，避免浪费
- **今日推荐**（10:35）：基于活动日历和可领券，智能生成用券建议

### 🎯 多模式支持
- 🏠 **个人独享**：通过 GitHub Actions 或本地脚本运行，适合个人使用
- 🤖 **Bot 共享**：搭建 Telegram Bot，朋友只需发送 Token 即可使用，适合分享给小白朋友

### 📱 完善的用户体验
- **消息格式优化**：移动端友好的消息布局，清晰的视觉分隔
- **多账号管理**：支持绑定多个麦当劳账号，随时切换
- **统计可视化**：查看领券成功率、成就系统
- **多渠道推送**：支持 Telegram, Bark, 飞书, Server酱等多种推送方式

### 💰 零成本部署
- 支持 GitHub Actions 免费运行
- 支持多种免费 PaaS 平台部署

---

## 🚀 快速开始

### 第一步：获取 MCP Token

1. 访问 [麦当劳 MCP 控制台](https://open.mcd.cn/mcp/console)。
2. 登录并点击“申请 Token”。
3. 复制获得的 Token（请妥善保管）。

麦当劳官方 MCP Server 信息：
- 接入地址：`https://mcp.mcd.cn/mcp-servers/mcd-mcp`
- 传输协议：Streamable HTTP
- 认证方式：在请求头中携带 `Authorization: Bearer YOUR_MCP_TOKEN`

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

### 2. 快速启动 (使用官方镜像)
不需要下载代码，直接创建一个 `docker-compose.yml` 文件：

```yaml
version: '3.8'
services:
  mcdonalds-bot:
    image: ghcr.io/noxenys/mcdonalds:latest # 也可以指定版本号，如 :2.0.11
    container_name: mcd_bot
    restart: always
    environment:
      - TG_BOT_TOKEN=你的BotToken
      - TZ=Asia/Shanghai
    volumes:
      - ./data:/app/data  # 挂载数据目录，防止重启丢失用户数据
```

然后在同级目录下运行：
```bash
docker-compose up -d
```
搞定！Bot 已经跑起来了。
### 3. (可选) 手动编译 (推荐)
如果你想使用最新代码，或者遇到依赖问题（如 `schedule` 模块缺失）：
1. `git clone` 本仓库。
2. 修改 `docker-compose.yml`，确保使用 `build: .` 而不是 `image: ...`。
3. `docker-compose up -d --build`。

---

## 💻 本地开发/运行 (Python)

如果你不使用 Docker，也可以直接运行 Python 脚本：

1. **环境准备**：确保安装 Python 3.10+。
2. **安装依赖**：
   ```bash
   pip install -r requirements.txt
   ```
3. **配置环境变量**：参照上方环境变量表进行设置（推荐创建 `.env` 文件）。
4. **启动**：
   ```bash
   python bot.py
   ```

---

## ☁️ 场景三：PaaS 部署 (Zeabur/Render)

本仓库针对 Zeabur 等 PaaS 平台进行了优化。

### Zeabur 部署

1. 在 Zeabur 中创建新项目。
2. 选择 **Deploy New Service** -> **Git**。
3. 选择你 Fork 的仓库。
4. Zeabur 会自动识别 `Dockerfile` 并开始构建。
   - **注意**：我们建议使用 **源码部署** (Source Code)，即让 Zeabur 读取仓库中的 `Dockerfile` 进行构建，以确保所有依赖被正确安装。
5. 在服务的 **Variables** 中添加环境变量：
   - `TG_BOT_TOKEN`: 你的 Telegram Bot Token
   - `TZ`: `Asia/Shanghai` (确保时间正确)
6. 部署成功！

### 版本化镜像发布（publish.sh）

- 你也可以使用仓库根目录下的 `publish.sh`：
  - 本地构建并推送镜像到 `ghcr.io/noxenys/mcdonalds`（同时更新 `latest` 与带时间戳的版本号）。
  - 自动更新 `docker-compose.yml` 中的 `image:` 标签为新版本，方便 Zeabur 等平台总是拉取最新镜像。

---

## 🛠️ 命令列表 / 使用方法
1. 在 Telegram 上找到你的 Bot。
2. 发送 `/start` 或 `/menu` 打开按钮菜单。
3. 直接发送 **MCP Token** 给 Bot，或使用 `/token <你的MCP Token>` 命令。
4. Bot 会验证 Token，如果正确，就会自动保存。
5. 之后你可以使用以下命令：
   - `/claim`：立即领券
   - `/coupons`：查看当前可领优惠券
   - `/mycoupons`：查看你已拥有的优惠券
   - `/calendar`：查看活动日历（可选参数 YYYY-MM-DD，自动生成 Telegraph 图文页并发送链接）
   - `/today`：基于活动日历与当前可领券，生成「今日用券建议」（优先生成 Telegraph 图文页，失败时发送纯文本）
   - `/status`：查看当前绑定状态、自动领券与汇报开关
   - `/stats`：查看自己的领券统计
   - `/autoclaim on` / `/autoclaim off`：开启或关闭每日自动领券
   - `/autoclaimreport on` / `/autoclaimreport off`：开启或关闭每日自动领券结果私聊汇报
   - `/account add/use/list/del`：多账号管理（支持为同一个人绑定多个麦当劳账号）
   - `/cleartoken`：清除当前账号的所有 Token 记录（等同 `/unbind`）
   - `/admin`：管理员总览（仅 TG_CHAT_ID 对应账号可用，支持 `/admin sweep` 立即执行一次全量自动领券，`/admin broadcast <消息>` 群发通知）

### 🤖 智能自动化时间表
Bot 会在以下时间自动为你服务：
- **10:30** - 自动领取所有可用优惠券
- **10:35** - 推送今日智能用券建议
- **11:30** - 午餐时间提醒（列出可用优惠券）
- **17:30** - 晚餐时间提醒（列出可用优惠券）
- **20:00** - 过期提醒（3天内即将过期的券）

**默认会私聊通知结果，你也可以通过 `/autoclaimreport off` 关闭自动汇报，仅在后台默默领券。**

### 👨‍👩‍👧‍👦 如何分享给朋友
你的 Bot 天生支持**多用户**！
1. 直接把 Bot 用户名转发给朋友。
2. 朋友点击 `/start`，并发送他们自己的 MCP Token。
3. Bot 会自动把他们加入数据库，每天早上也会帮他们自动领券。
4. 你作为管理员，可以通过 `/admin` 查看有多少朋友在使用。

---

## ☁️ 场景三：免费 PaaS 部署 (无需懂服务器/Docker)

如果你不懂 Docker 或者没有服务器，这是最简单的方案。
**注意：** 免费实例重启后可能会丢失数据库（丢失已绑定的朋友），建议使用外挂数据库或定期备份。

### 🚀 Hugging Face Spaces

1. 创建一个新的 **Space**，SDK 选择 **Docker**。
2. 将本仓库代码上传（或 Clone）。
3. 在 Space 的 **Settings** -> **Variables and secrets** 中添加：
   - `TG_BOT_TOKEN`: 你的 Telegram Bot Token
   - （推荐）`DATABASE_URL`: 你的 PostgreSQL 连接串（例如 `postgresql://user:password@host:5432/dbname`），用于持久化存储用户数据
   - （可选）`DB_PATH`: SQLite 数据库路径，默认 `users.db`。如果你在 HF 开启了 Persistent Storage，请设为 `/data/users.db`，把数据库放到持久盘中
4. 如果你**不配置 `DATABASE_URL` 且未使用 Persistent Storage**，Space 重启或重置后本地 SQLite 文件会被清空，所有已绑定的用户都会丢失，仅适合测试使用。
5. 部署完成后，Bot 即可在线。

### 🚀 Koyeb

1. 注册 Koyeb 并创建新 App。
2. 选择 **GitHub** 部署，连接你的仓库。
3. 在 **Environment Variables** 中添加：
   - `TG_BOT_TOKEN`: 你的 Telegram Bot Token
   - `DATABASE_URL`: 你的 PostgreSQL 连接串（例如 `postgresql://user:password@host:5432/dbname`）
   - `TZ`: `Asia/Shanghai` (可选)
   - `PORT`: `8000` (可选，Koyeb 通常会自动注入)
4. 部署即可。**强烈建议配置 `DATABASE_URL`，因为 Koyeb Free 实例不支持挂载 Volume，直接使用 SQLite 会导致重启后数据丢失。**

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
| `MCD_TOKEN_SECRET` | ❌ | Token 加密密钥（开启后将加密保存 Token；务必长期固定，否则已加密 Token 无法解密） |
| `TG_CHAT_ID` | ❌ | Owner 的 Telegram Chat ID (用于自动绑定 Owner Token) |
| `DATABASE_URL` | ❌ | PostgreSQL 数据库连接串 (例如 `postgres://...`)，配置此项后将不再使用 SQLite，适合 Koyeb 等无持久化存储的平台。 |
| `DB_PATH` | ❌ | SQLite 数据库路径，默认 `users.db`。Zeabur/Northflank 请设为 `/app/data/users.db` |
| `TZ` | ❌ | 时区设置，默认 `Asia/Shanghai`。Docker 部署建议检查此项。 |
| `BARK_KEY` | ❌ | Bark 推送 Key (Actions 模式用) |
| `FEISHU_WEBHOOK` | ❌ | 飞书 Webhook (Actions 模式用) |
| `SERVERCHAN_SENDKEY` | ❌ | Server酱 SendKey (Actions 模式用) |

### 🔐 安全说明 (MCD_TOKEN_SECRET)

配置 `MCD_TOKEN_SECRET` 后，系统会对存储在数据库中的 Token 进行加密保护：
- **加密机制**：使用 SHA256(Secret) 作为密钥，对 Token 进行 XOR 运算并 Base64 编码。
- **数据形态**：数据库中存储的 Token 将以 `enc:` 开头。
- **⚠️ 重要警告**：
  - 务必长期固定使用同一个 Secret。
  - **如果更改或丢失 Secret，所有已存储的加密 Token 将无法解密，导致无法领券。**
  - 如需轮换密钥，必须先清空数据库或手动迁移数据。

### ⚠️ 注意事项

1. **Token 过期**：麦当劳 Token 可能会过期。如果过期，Bot 会在每天的推送中提示你 `Token invalid`，并自动暂停该账号的每日自动领券以减少无效请求。此时请重新发送新 Token 给 Bot，或在更新 Token 后使用 `/autoclaim on` 重新开启自动领券。
2. **时区问题**：程序默认设定每天 10:30 运行。Docker 镜像已内置 `Asia/Shanghai` 时区，确保你领券是在白天而不是半夜。
3. **隐私安全**：Bot 的数据库存储了用户的 Token。请确保你的数据库文件（`users.db`）安全，不要分享给他人。
4. **加密密钥**：如果配置了 `MCD_TOKEN_SECRET`，请长期固定该值；更改或丢失会导致已加密 Token 无法解密。

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

MIT License
