# X Vault

把公开的 X（Twitter）帖子、Thread、图片，以及 TikTok 公开视频保存到自己的空间。**个人自部署、单用户使用**，支持 iPhone 快捷指令分享收藏，以及可选的 Notion 同步。

- **iPhone 一键收藏**：从 X 分享到「收藏到 X Vault」，无需切换 Safari。收到「收藏完成」才表示 Notion 已写入；「正在处理」不代表完成。
- **图文与 Thread**：解析正文和可获取的媒体；同作者 Thread 尽力还原，并非保证抓取完整。
- **视频**：新版「视频存本机」快捷指令识别到视频后，默认下载 MP4/WebM（多视频或部分失败时保存 ZIP）到 iPhone 的「文件」；图文仍自动存 Notion。旧版快捷指令仍可选择把视频正文、封面及原帖链接存 Notion。
- **TikTok 公开视频**：网页粘贴常规链接或 vm/vt 短链，解析后下载 MP4 等视频到设备；新版「视频存本机」iPhone 快捷指令也支持从 TikTok 分享后本地下载，不写入 Notion。不支持私密内容、图集或直播。
- **网页连接 Notion**：填入一次内部连接密钥和收藏页面链接，后续快捷指令不用改。

## 1. 部署 X Vault

推荐使用 [Render](https://render.com/) Docker Web Service：

1. Fork 本项目，在 Render 新建 **Blueprint**，连接仓库的 `render.yaml`；也可以手动创建 Docker Web Service。
2. Render → Environment 设置 `APP_TOKEN`（至少 24 位随机字符）。Blueprint 若已生成，可沿用它。其他 Notion 变量可以先不填。
3. 部署后访问 `https://你的服务名.onrender.com/health`，确认返回 `"ok": true`；再打开服务首页，输入 `APP_TOKEN`。此健康检查也支持 UptimeRobot 免费版默认的 HEAD 请求。

详细步骤见 [Render 部署说明](RENDER-DEPLOY.md)。

## 2. 连接自己的 Notion

无需注册公共 OAuth 应用，也不需要 Client ID、Client Secret、回调地址。

1. 在 [Notion 我的集成](https://www.notion.so/profile/integrations) 创建**内部连接**，打开读取、插入、更新内容权限，并复制密钥。
2. 在 Notion 新建普通页面（例如「X 收藏」），在页面的「··· → 添加连接」中授权刚创建的连接访问该页，然后复制该页面链接。
3. 回到 **X Vault 首页 → 连接 Notion**，粘贴连接密钥和页面链接，点击 **验证并保存**。

网页自动提取 Page ID、验证密钥能否读取该页面，然后加密保存在服务器的 SQLite。**验证只检查读取权限；如果未开启插入权限，实际收藏时仍可能失败。** Notion 密钥不会返回浏览器，也不要把它提交到 GitHub。

**Render 免费版建议保留备用环境变量**：`NOTION_TOKEN` 和 `NOTION_PARENT_PAGE_ID`。如果网页配置与环境变量同时存在，优先使用网页配置；当免费实例丢失本地 SQLite 后，可回退到环境变量配置。不需要在 Render 修改配置才能使用网页连接功能。

## 3. 在 iPhone 的 X 中收藏

**新版推荐：视频默认本地下载。** 在仓库的 `ios/build_shortcut_video.py` 可生成新版快捷指令，运行脚本后用 macOS `shortcuts sign --mode anyone` 签名（需登录 iCloud），导入时填入自己的 Render API 地址与 `APP_TOKEN`。视频自动识别后从服务端获取一次性下载链接，由快捷指令下载并弹出 iOS「存储文件」面板，选择「我的 iPhone」中的目录并保存。普通图文继续存到 Notion。**文件实际保存完成前不视为成功**；大视频可能受 Render 网络和 iOS 快捷指令运行时间限制。

仍可继续使用原来的「收藏到 X Vault」快捷指令，它不会被后端强制变更行为：视频仍只写入 Notion 的正文、封面与原帖链接。想手动创建新版快捷指令，需要在下面原有步骤的 JSON 请求正文里额外添加 `video_action` = `download`，并根据返回的 `status=download_ready` 取 `download_url`，GET 下载文件后使用「存储文件」动作。新用户如果只要 Notion 收藏，也可以沿用原版：

1. 添加 **获取 URL 内容**：地址设为 `https://你的服务名.onrender.com/api/shortcut/save`，方法 `POST`，JSON 请求正文增加 `url`，值设为蓝色变量「快捷指令输入」。
2. 请求头添加 `Authorization`，值为 `Bearer 你的APP_TOKEN`（Bearer 后有空格）。
3. 添加 **获取字典值**，从上一步响应读取键 `message`；再添加 **显示通知**，内容选择这个字典值。**不添加「打开 URL」**。

使用时，在 X 帖子中打开系统分享菜单 →「收藏到 X Vault」。服务会返回「收藏完成」「正在处理」或失败原因。Notion 尚未连接时，请打开 X Vault 网页进行设置，不用改快捷指令。

## 4. TikTok 视频解析下载

打开 X Vault 网页首页的「TikTok 视频解析下载」，粘贴 `https://www.tiktok.com/@用户/video/数字ID` 或 `https://vm.tiktok.com/...` / `https://vt.tiktok.com/...` 公开视频分享链接，点击「解析视频」查看作者/标题/时长，再点「下载到本机」。服务器生成 **5 分钟有效、仅可使用一次**的临时下载链接；完成传输后清理暂存文件，不写入 Notion，也不会存入 X 收藏数据库。

已安装新版「视频存本机」快捷指令的 iPhone 用户可以直接从 TikTok 分享：新版服务会识别 TikTok 并交给系统「存储文件」。如果仍用旧版 Notion 快捷指令，则会提示更换新版。TikTok 解析依赖 yt-dlp；若需要登录、存在地区限制、被封禁或风控拦截，无法保证下载成功。服务端当前单视频文件大小上限 **150 MB**（如 `MAX_FILE_MB` 更低，则以更低值为准）；大视频也可能超过 Render Free 的请求与资源限制。

## 5. 不使用 Render：Docker 本地部署

```bash
cp config.example .env
openssl rand -hex 32       # 将生成的字符串填入 .env 的 APP_TOKEN
docker compose up -d --build
```

然后访问 `http://localhost:8000`。本地 Docker 默认使用命名卷保存 `/data`，容器重新构建后仍保留归档；备份示例：

```bash
docker compose exec -T x-vault tar -czf - -C /data . > xvault-backup.tar.gz
```

远程手机分享需要公网可访问的 HTTPS 地址；请参照 [部署说明](RENDER-DEPLOY.md) 配置域名或反向代理。

## 注意事项

- **Render Free 没有持久磁盘**：实例休眠、重启或重新部署后，本地 SQLite、已下载媒体、待处理队列以及网页保存的 Notion 设置可能丢失；已经写入 Notion 的内容仍在 Notion。需要可靠长期归档，应选用持久化存储。
- 第三方 X 解析接口可能故障、限流或缺失 Thread；受保护、已删除及限制访问的帖子不保证成功。
- 视频下载需要服务器带宽及 FFmpeg；免费服务有资源和文件大小限制。Notion 文件上传受账户额度限制。
- `APP_TOKEN` 是整个私人服务的访问密钥，请妥善保管。网页保存的 Notion 密钥使用其派生密钥加密，因此**更换 APP_TOKEN 后需要重新保存 Notion 配置**。
- 本项目用于保存你有权访问和使用的内容，不绕过 DRM、私密账号或付费限制。

## 技术依赖与致谢

感谢以下项目和服务为 X Vault 提供基础能力：

- [FxEmbed / FxTwitter](https://github.com/FxEmbed/FxEmbed)：公开 X 帖子及 Thread 元数据解析（第三方服务，非 X 官方 API）。
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)：X、TikTok 公开视频提取与下载。
- [FFmpeg](https://ffmpeg.org/)：视频转码和音画合并。
- [FastAPI](https://fastapi.tiangolo.com/)：Web API 框架。
- [Notion API](https://developers.notion.com/)：个人收藏页面同步。

## 开源许可

X Vault **自有代码**采用 [MIT License](LICENSE)。第三方项目、API 与软件包保留各自的许可及使用条款；尤其 FFmpeg 的许可证取决于实际构建方式。X Vault 不隶属于 X、Notion 或上述项目。

## 测试

```bash
pip install -r requirements.txt pytest httpx
pytest -q
node tests/share_flow.test.cjs  # 可选：网页版分享流程测试
```

测试使用模拟数据；真正上线后还需用自己的 Render 服务、Notion 页面和公开 X 帖子进行一次端到端测试。