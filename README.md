# X Vault · 免费自托管 X 帖子/Thread/图片/视频归档工具

> 版本：1.0 · Python FastAPI + FxTwitter + yt-dlp/FFmpeg + SQLite + Docker + Notion
>
> 适用：个人收藏公开 X 帖子、长帖，自动下载能访问到的高清照片和视频，无需在 X 公开回复。
>
> **不是绕过私密账号、付费限制或 DRM 的工具。** “最高画质”是 X 对外提供的最佳可用转码版本，无法保证是作者初始上传的原始素材。

## 视频处理新流程（按需保存）

- 提交 X 链接后先解析帖子元数据，不下载视频；若识别到视频，明确展示「保存到 Notion」与「下载视频到本机」两个按钮。
- **本机下载**：临时获取视频；一个无错误的视频直接下载 MP4/WebM，多个视频或部分失败时下载 ZIP（含完整状态 JSON、Markdown 和成功视频）；传输完成后清理服务器临时目录。下载凭据有效 5 分钟且仅能使用一次，移动网络下载期间不要关闭页面。
- **保存到 Notion**：需配置 Notion 集成。服务器仅暂存视频文件用于上传，上传后删除；Notion 免费账户单文件大小限制依然有效。视频过大或上传失败会在任务状态中标记，**不会**把未上传的文件当作成功。帖子文字和上传结果元数据仍保存在服务器。
- **非视频图文**：沿用原有保存逻辑。旧版已下载到服务器的视频不会自动删除，避免误删已有数据，需自行确认后清理。
- 服务端下载过程需要临时磁盘空间与带宽；并非真正零写盘，但完成后不长期保留视频。中断或崩溃遗留的临时目录在下次启动时清理。
- 旧版 iPhone 快捷指令仍可调用 `/api/save`；遇到视频时返回 `needs_choice`，这时需要打开网页版选择去向。快捷指令必须适配该返回值，不能把它当作「已归档」。

## 功能

- X 公共帖子 URL：自动解析作者、发布时间、正文、同作者的 Thread（第三方接口无法保证每次完整）
- 图片：下载高清图片（FxTwitter 所返原始分辨率 URL）；包含可解析的引用帖正文与媒体
- 视频：依序尝试高清 MP4/WebM；若只有 HLS，使用 yt-dlp + FFmpeg 下载并合并；可选在失败时调用 yt-dlp 的 X 提取器
- 每条帖子保存到 **SQLite + JSON + Markdown + 图片/真实视频文件**，视频不仅是一个链接
- 失败消息明确记录；持久队列；容器重启恢复排队任务；重复收藏不重复抓取，支持手动重试
- 自动创建 Notion 子页面，上传完整文本和限额内图片/视频。**免费 Notion 的文件大小上限是每文件 5 MiB**；付费 Notion 最大单文件 5 GiB，超过 20 MiB 分片上传。
- 单用户 Token 保护 API；每条公开媒体下载链接使用独立的随机 256-bit 链接密钥
- 手机：iOS 快捷指令分享；Android Chrome 可添加到主屏幕后从其他 App 系统分享进入；电脑网页版

## 1. 本地运行（约定机器已有 Docker）

```bash
cp config.example .env
openssl rand -hex 32
```

打开 `.env`，把上面随机字符串填到 `APP_TOKEN=` 后面。测试时，Notion 配置先留空。

```bash
docker compose up -d --build
# 查看健康状态
curl http://localhost:8000/health
```

浏览器打开 `http://localhost:8000`，第一次粘贴 `.env` 里的 `APP_TOKEN`，保存；粘贴 X 帖子 URL 并提交。归档是队列模式；在收藏记录列表可刷新、查看失败消息、重试及下载真实文件。

**重要：** `.env` 是机密配置，不要传入 Git。归档文件位于 Docker 命名卷 `xvault_data`，容器重建不会丢失。

查看日志：

```bash
docker compose logs -f x-vault
```

备份所有数据库和视频（当前项目目录执行）：

```bash
docker compose exec -T x-vault tar -czf - -C /data . > xvault-backup.tar.gz
```

## 2. 连接 Notion

1. 打开 Notion 开发者页面 https://www.notion.so/profile/integrations （若路径变化可从 Notion → Settings → Connections 进入）。创建内部集成，授予 Insert content、Update content 和 Read content 能力。
2. 新建一个名为「X 收藏」的 Notion 普通页面，通过页面右上角 `...` → Connections 将你的集成加入该页。
3. 复制 Notion 集成 Token，填入 `.env` 中的 `NOTION_TOKEN`。
4. 复制父页面 URL 中的页面 ID（32 个十六进制字符或带连字符 UUID），填入 `NOTION_PARENT_PAGE_ID`。
5. 重启：`docker compose up -d --force-recreate`。
6. 新收藏的帖子会在该父页面下生成子页面。若只想本地保存，就留空两个 Notion 配置。

**注意：** Notion 页面创建后会立即保存地址至 SQLite，避免后续填充失败时重复创建。现有页面不会在自动重试时重新填充，需人工补齐。

## 3. 将手机 X 一键分享到归档服务

要让手机在外网访问，必须提供有效 HTTPS 域名。对于有公网 IP 的自托管服务器，可用 Caddy + DNS-only 记录：

1. 在域名 DNS 解析处为 `archive.example.com` 添加 A/AAAA 记录指向自己服务器公网地址；若使用 Cloudflare DNS，此记录设为 **DNS only（不代理）**。大文件媒体经 Cloudflare 普通代理分发可能有额外服务条款限制。
2. 在 `.env` 中添加 `DOMAIN=archive.example.com` 及 `PUBLIC_BASE_URL=https://archive.example.com`。
3. 服务器开放 80/443 端口；运行以下命令，Caddy 会申请 Let's Encrypt HTTPS 证书：

```bash
docker compose -f compose.yaml -f compose.public.yaml up -d --build
```

**iPhone/iPad：X 内一键收藏，不跳转 Safari。**

首次在 Render → Environment 中配置 `NOTION_TOKEN`、`NOTION_PARENT_PAGE_ID` 和 `APP_TOKEN`。已部署到 Render 的服务须拉取最新 GitHub 代码重新构建。

1. iPhone 打开「快捷指令」→ 「+」→ 命名为「收藏到 X Vault」。在详情里开启「在共享表单中显示」，接收「URL」和「文本」。
2. 添加「获取 URL 内容」动作：URL 填 `https://你的Render域名.onrender.com/api/shortcut/save`；方法选择 `POST`；请求正文 `JSON`，增加字段 `url`（文本类型）并选择蓝色的「快捷指令输入」变量。
3. 同一个「获取 URL 内容」动作里添加请求头：名称 `Authorization`，值为 `Bearer 你的APP_TOKEN`（Bearer 后有一个空格）。如需要，再添加 `Content-Type` = `application/json`。
4. 添加「获取字典值」动作，从上一步「URL 的内容」结果中读取键 `message`。
5. 添加「显示通知」动作，通知正文选择上一步取得的 `message` 值。不添加「打开 URL」动作。
6. X → 帖子「分享」→ iOS 系统共享菜单 → 「收藏到 X Vault」。快捷指令保持在共享表单中，结果可能是「✅ 收藏完成」「⏳ 正在保存」或「⚠️ 收藏失败」；**只有服务端确认 Notion 同步成功才会显示完成**。

新接口 `POST /api/shortcut/save` 会提取分享文本中的 X 帖子 URL，普通图文自动入队上传 Notion；含视频的帖子仅保存正文、可用封面外链和原帖播放链接，**不会下载或上传视频文件**，减少 Render 和 Notion 占用。后台最多等待 25 秒：若尚未完成，则返回「处理中」而非误报成功。Render Free 冷启动、分享任务较慢或快捷指令被中断时可能需要稍后查看 Notion。

**安全提醒：** APP_TOKEN 仅放在个人快捷指令请求头中，不放在 URL、公开仓库或截图里。使用 iCloud 分享快捷指令时可能连同 Token 一并分享，务必删除密钥后再分享。旧版在网页分享时仍可通过 `?url=` 自动解析，视频可手动选择上传或下载。

如果 X 分享菜单没有出现快捷指令，确认「在共享表单中显示」开启并允许「URL」「文本」；某些 X 版本需要先打开系统共享菜单。

**Android：** Chrome 访问 HTTPS 的 X Vault → 菜单「添加到主屏幕」或「安装应用」；在 X App 分享帖子时，可以在 Android 分享目标中选择 X Vault，应用收到分享 URL 后需要点击一次「立即归档」。分享目标功能需 Android/浏览器实际支持。

**电脑：** 打开归档首页，粘贴 `https://x.com/.../status/...`；未来可以添加书签脚本。

## 4. 直接调用 API（例如手机自动化）

```bash
curl -X POST 'https://archive.example.com/api/save' \
  -H 'Authorization: Bearer 你的APP_TOKEN' \
  -H 'Content-Type: application/json' \
  --data '{"url":"https://x.com/someone/status/1234567890"}'

curl -H 'Authorization: Bearer 你的APP_TOKEN' \
  'https://archive.example.com/api/archives'
```

`POST /api/save` 返回 `queued` 表示已排队，**不能证明下载完成**。列表中的 `complete` 表示数据和成功媒体完成；`partial` 表示部分媒体或同步失败；`failed` 表示归档整体失败。

## 5. 数据与隐私

- `/data/archives/{tweet_id}/archive.json`：结构化内容和媒体成功/失败状态
- `/data/archives/{tweet_id}/archive.md`：易于迁移的 Markdown
- `/data/archives/{tweet_id}/media/`：独立保存的真实高清图片/视频
- `/data/vault.sqlite3`：任务、下载状态、Notion 页面 URL
- 如设置 `PUBLIC_BASE_URL`，Notion 中的大文件下载链接含随机访问凭据。任何获得该链接的人都可获取对应媒体；如果不希望产生这类链接，保持 `PUBLIC_BASE_URL` 为空，视频仍保存在自建服务器，需从个人 Web 控制台下载。
- 服务端仅允许 X/Twitter 帖子 URL 和可信媒体域名，避免任意 URL 服务端抓取。请勿将 `APP_TOKEN` 和 Notion Token 提交到公共 GitHub 仓库。
- 对受保护/已删除/被地区限制的帖子无法保证提取；第三方 FxTwitter 可停机、更改字段或限流，遇到失败状态会显式记录。
- **“完整 Thread”是 best effort，不是对 X 的所有回复和引用作完整抓取**；同作者 Thread 由 FxTwitter 解析，返回的帖子顺序将按发布时间排列。
- 归档仅限你有权保存和使用的内容；不提供解密 DRM 或绕过受保护页面的能力。

## 6. 常见问题

**视频是不是原视频？** 是 X 公开提供的最高可获取清晰度转码文件，不保证与上传前源文件字节完全相同。

**是永久免费吗？** 软件、Docker、SQLite、FxTwitter 公共接口目前无需订阅费；服务器/域名/带宽/存储自行承担费用，第三方接口可变。Notion 免费额度也有限制。

**为何不用 Workers？** Worker + R2 更适合小文件和直接存储，HLS 合并依赖 FFmpeg、多个分段和长时间操作，Docker 更容易可靠实现并支持较大视频。Cloudflare Tunnel 可用于管理访问，但公开分发较大视频时应遵守 Cloudflare 对视频流量的规则。

**为什么有 `partial`？** 这是刻意保留的真实性标记；图片下载失败、Thread API 没有返回完整内容、Notion 额度不够都会导致不完整。即使 Notion 失败，已经成功下载的本地文件仍然存在。

## 7. 测试

如已有 Python + pytest + requests + fastapi：

```bash
pip install -r requirements.txt pytest httpx
pytest -q
```

单元测试使用模拟返回数据，不需要真实 X、Notion 凭据。**部署后仍需要用你自己的公开 X 帖子做端到端实测**，确认 X 接口和 CDN 在你的服务器网络上可访问。

官方参考：

- FxEmbed OpenAPI: https://github.com/FxEmbed/FxEmbed/blob/main/docs/specs/fxtwitter-openapi.json
- Notion File Uploads: https://developers.notion.com/reference/create-file
- Notion 文件限额: https://developers.notion.com/guides/data-apis/working-with-files-and-media
- Cloudflare Workers Streaming: https://developers.cloudflare.com/workers/best-practices/workers-best-practices/