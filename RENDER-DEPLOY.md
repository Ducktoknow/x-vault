# Render 部署 X Vault（个人使用）

## 首次部署

1. Fork 本项目到自己的 GitHub。
2. 打开 [Render Dashboard](https://dashboard.render.com/) → **New → Blueprint**，选择这个仓库，使用根目录 `render.yaml`；或使用 **New → Web Service**，选择 **Docker** 和 **Free**。
3. 在 Render → **Environment** 检查 `APP_TOKEN`：至少 24 位随机字符。自动生成的密钥无法查看时，可自己生成替换。**不要把它放进 GitHub**。
4. 等待部署完成，访问 `https://你的服务名.onrender.com/health`，确认 `ok: true`。打开首页输入 `APP_TOKEN`。

## 连接 Notion（无需额外 Render 配置）

- 在 Notion 新建**内部连接**，授权它访问自己的「X 收藏」普通页面。
- 打开 **X Vault 首页 → 连接 Notion**，粘贴 Notion 内部连接密钥及收藏页面链接，点击「验证并保存」。
- **已经跑通的 iPhone 快捷指令不需要修改**。它仍然调用 `POST /api/shortcut/save`。

### Render 免费版的备用方案

如果希望网页连接设置在 Render 实例数据丢失后仍能使用，可在 Environment 保留以下两个变量：

| 环境变量 | 填写内容 |
| --- | --- |
| `NOTION_TOKEN` | Notion 内部连接密钥 |
| `NOTION_PARENT_PAGE_ID` | 收藏页的 32 位 Page ID |

网页保存的设置优先；网页配置不存在时，自动使用以上环境变量。修改这两个环境变量会触发重新部署，但不影响已保存到 Notion 的页面。

## 部署更新

GitHub `main` 更新后，如果 Render 启用了自动部署，会自动构建；否则在 Render → **Manual Deploy → Deploy latest commit**。

请注意 **Render Free 的本地磁盘不持久**：实例休眠、重启、重新部署或被替换时，SQLite、已下载文件及网页保存的连接都可能丢失。要持久保存请使用有持久化存储的服务；Notion 中已经写入的归档不受影响。

## TikTok 视频解析下载

最新版 X Vault 首页有独立的 TikTok 视频下载入口：粘贴公开视频链接或 vm/vt.tiktok.com 分享短链 → 解析 → 下载本机。视频仅短暂保留在 Render 临时目录，传输完成即删除，不保存到 Notion；新版「视频存本机」快捷指令也可从 TikTok 分享。若遇到登录、验证码、地区限制或平台限制，可能无法解析，无法绕过受保护视频；大视频尤其可能受免费实例内存和请求时间限制。

## iPhone 分享快捷指令分工

本项目提供「收藏到 X Vault · 稍后看」与「X Vault · 下载视频到相册」两个入口。两者使用相同的 `POST /api/shortcut/save`，但是下载快捷指令传入 `video_action=download_only`；收藏快捷指令不传 `video_action`。TikTok 收藏现在也支持 Notion，需要 `NOTION_TOKEN`、`NOTION_PARENT_PAGE_ID` 或网页连接成功。两种直链都可能过期，因此 Notion 同时保存原帖稳定链接。

## UptimeRobot 免费版监测

UptimeRobot 的 **HTTP(s)** 监控默认使用 **HEAD**；X Vault 的 `/health` 已同时支持 GET 与 HEAD，两者均返回 HTTP 200，因此**不需要购买 GET 请求功能**：

- Monitor Type：`HTTP(s)`
- URL：`https://你的服务名.onrender.com/health`
- Monitoring Interval：`5 minutes`
- HTTP Method：保留默认 `HEAD`；无需 APP_TOKEN 或请求头

也可以自己验证：`curl -I https://你的服务名.onrender.com/health` 应返回 `HTTP 200`。更新后需让 Render 部署**最新代码**才会生效。

定期请求通常可减少因空闲而休眠，但并不能避免 Render 主动重启、免费额度耗尽、实例替换或本地数据丢失；**保活不等于备份**。

## 排查

- **401**：检查请求头 `Authorization: Bearer APP_TOKEN` 是否正确，密钥与 Render Environment 中一致。
- **缺少 Notion 配置**：回到首页「连接 Notion」，确认网页状态为「已连接」，并在 Notion 原页面中添加过内部连接。
- **Notion 写入失败**：内部连接至少需要读取、插入、更新内容的权限。验证按钮只确认连接有权读取页面；真正的写入错误会在收藏结果中显示。
- **视频/帖子解析失败**：先用公开帖子测试，检查 Render Logs；FxTwitter、X CDN 和第三方接口可能限流或暂时不可用。
- **任务长时间处理中**：免费实例冷启动和网络延迟可能超过快捷指令等待时间；「正在处理」不是「收藏成功」。

### 在自己的服务器用 Docker

```bash
cp config.example .env
# 设置 .env 的 APP_TOKEN（至少 24 位）
docker compose up -d --build
```

打开 `http://localhost:8000`。数据默认保存在 Docker 命名卷 `xvault_data`；如要让外网 iPhone 分享到这台服务器，请为它配置 HTTPS。