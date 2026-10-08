# Render Docker 部署指引（免费版）

## 1. 准备 GitHub 仓库
- 把 x-vault 目录作为**独立仓库根目录**提交：根目录必须能找到 `Dockerfile`、`render.yaml`、`requirements.txt`、`src/` 和 `web/`。
- 不要提交 `.env`、Notion Token、Python 虚拟环境或本地归档文件（已配置 .gitignore 和 .dockerignore）。
- 如果整个 codex 文件夹作为仓库：在 Render 新建 Web Service 时将 Root Directory 设为 `x-vault`，或在 Blueprint 中调整路径；最简单的是独立仓库。

## 2. 创建 Render Web Service
- Render Dashboard → New → Blueprint，连接 GitHub 仓库，选择根目录 `render.yaml`。或 New → Web Service → 选择仓库 → Language/Runtime 选择 Docker → Instance Type 选择 Free。
- Blueprint 包含健康检查 `/health`、`PORT=10000`、`APP_TOKEN` 自动生成、`DATA_DIR=/data`、下载上限 `MAX_FILE_MB=100`。如使用手动 Web Service，需要自行配置这些环境变量。
- **APP_TOKEN 必须妥善保管**。自动生成的凭据请在 Render Environment 查看并保存在密码管理器；若界面不能显示完整自动值，可自己生成至少 24 位随机 Token，然后在 Render 设置。
- 可选：在 Environment 设置 `NOTION_TOKEN` 和 `NOTION_PARENT_PAGE_ID`，两者同时设置才会上传到 Notion。
- 不要使用 `compose.yaml` 创建 Render 服务：Render 直接依据 Dockerfile 构建，不需要 Docker Compose。
- 部署完成后访问 `https://<your-service>.onrender.com/health` 检查 JSON 中 `ok: true`，再打开首页输入 APP_TOKEN。

## 3. 手机收藏
- 在 X App 中复制/分享帖子链接，打开 Render 提供的 HTTPS 网页，粘贴后先解析。
- 图文按当前逻辑归档；视频必须选择「保存到 Notion」或「下载到本机」。
- iOS 快捷指令若沿用旧版只请求 `POST /api/save`，遇到视频会收到 `needs_choice`，并**不会自动下载**。快捷指令需读取结果并打开 X Vault 页面进行选择。
- 视频下载为临时中转；首次唤醒冷启动、视频转码和大文件传输都可能超时。在免费计划上优先测试小视频，若下载不稳定建议升级或调整为直接从 X CDN 下载。

## 4. 免费实例的重要限制
- Free Web Service 闲置约 15 分钟后休眠，下次请求冷启动。
- Free 无持久磁盘，重启/重新部署/休眠导致本地 SQLite、图片和归档历史可能消失；Notion 中**已经成功上传**的数据才可视为外部持久副本。不要依赖本地任务队列保证免费实例上的长期一致性。
- 下载到设备的视频经过 Render 中转，会占用内存、CPU、临时存储和出站流量；视频文件仅在传输完成后清理，进程被杀时由下次启动清理。
- MAX_FILE_MB 默认为 100 MiB（按单文件限制）；超长视频、HLS 合并、内存和 Render 请求时限须进一步实测。
- 大于 Notion 账户允许的单文件上限的视频无法成功上传 Notion，会标为不完整；改为下载本机。
- 本方案适合功能验证、轻量私人使用；如果要求永久保存记录，建议使用持久化数据库与对象存储或付费实例加持久磁盘。

## 5. 快速排查
- 构建失败：确认仓库根路径与 Dockerfile 一致，查看 Render build logs。
- 未正常监听：确认 Render PORT=10000，Dockerfile 已使用环境端口。
- 401：检查 Bearer Token 是否与 Render Environment 的 APP_TOKEN 完全一致。
- 无法抓取 X：检查 logs 中 FxTwitter / yt-dlp 错误；第三方公共 API 有地区、频率或稳定性限制。
- 视频失败：先试较小公开 MP4；如果 HLS 视频超时，Free 实例可能不足。