# X Vault

把公开的 X（Twitter）帖子、Thread、图片，以及 TikTok 公开视频保存到自己的空间。**个人自部署、单用户使用**，支持 iPhone 快捷指令分享收藏，以及可选的 Notion 同步。

- **iPhone 一键收藏**：从 X 分享到「收藏到 X Vault」，无需切换 Safari。收到「收藏完成」才表示 Notion 已写入；「正在处理」不代表完成。
- **图文、Thread 与 X Articles**：解析正文与可获取的媒体；X Articles 长文优先提取标题和完整正文（包括常见标题、列表、引用和文章配图链接），写入 Notion。第三方接口无法返回文章正文时会报告归档不完整，不把原文链接冒充全文；Thread 也不保证抓取完整。
- **视频**：新版「视频存相册」快捷指令识别到 MP4 视频后自动存入 iPhone「照片」，ZIP/WebM 等文件仍回退保存到「文件」；X 图文继续存 Notion。旧版快捷指令依然可用。
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

## 3. iPhone 两个独立快捷指令（推荐）

**不需要弹出选择菜单。** 在 iPhone 的 X / TikTok 分享菜单里，按当下意图直接选其中一个：

- **「收藏到 X Vault · 稍后看」**：X 图文、长文、视频，以及 TikTok 公开视频都保存到 Notion，不下载视频。X 视频保存正文、封面、原帖、可解析的视频 MP4/HLS 直链；TikTok 保存标题、作者、原视频、封面和可解析的 MP4 直链。
- **「X Vault · 下载视频到相册」**：只下载 X/TikTok 视频；MP4 保存到 iPhone 相册，非 MP4（ZIP/WebM 等）保存到「文件」。对不含视频的 X 帖子明确报错，不会误收藏。

**视频 CDN 直链可能很快过期**，且有些链接需要特定请求头，不能代替稳定的原帖/视频链接；Notion 中两种链接都会保留。短视频不会被上传到 Notion，也不会被下载到 Render 的长期存储。

仓库提供两个快捷指令的生成脚本：`ios/build_shortcut_later.py` 和 `ios/build_shortcut_video.py`。在 Mac 项目根目录运行：

```bash
python3 ios/build_shortcut_later.py
python3 ios/build_shortcut_video.py
shortcuts sign --mode anyone --input ios/X-Vault-Read-Later.unsigned.shortcut --output ios/X-Vault-Read-Later.shortcut
shortcuts sign --mode anyone --input ios/X-Vault-Video-Photos.unsigned.shortcut --output ios/X-Vault-Video-Photos.shortcut
```

通过 AirDrop 将两个已签名 `.shortcut` 发送到 iPhone，分别导入。导入时都要填写自己的 Render 接口 `https://你的服务名.onrender.com/api/shortcut/save` 和 `APP_TOKEN`（仅输入密钥，不加 `Bearer`）。文件不提交 GitHub，避免泄露个人 Token。

旧版「收藏到 X Vault」「视频存本机」快捷指令继续可用，但想要清晰区分下载和收藏，建议使用上述两个新入口。**首次给 TikTok 收藏时应确认 Notion 连接正常。** Render Free 的 SQLite 重置后，可通过 `NOTION_TOKEN` / `NOTION_PARENT_PAGE_ID` 环境变量回退。

## 4. TikTok 视频解析下载

打开 X Vault 网页首页的「TikTok 视频解析下载」，粘贴 `https://www.tiktok.com/@用户/video/数字ID` 或 `https://vm.tiktok.com/...` / `https://vt.tiktok.com/...` 公开视频分享链接，点击「解析视频」查看作者/标题/时长，再点「下载到本机」。服务器生成 **5 分钟有效、仅可使用一次**的临时下载链接；完成传输后清理暂存文件，不写入 Notion，也不会存入 X 收藏数据库。

已安装「下载视频到相册」快捷指令的 iPhone 用户可以直接从 TikTok 分享并下载；若只想以后再看，则选「收藏到稍后看」，不会下载视频。如果仍用旧版 Notion 快捷指令，则会提示更换新版。TikTok 解析依赖 yt-dlp；若需要登录、存在地区限制、被封禁或风控拦截，无法保证下载成功。服务端当前单视频文件大小上限 **150 MB**（如 `MAX_FILE_MB` 更低，则以更低值为准）；大视频也可能超过 Render Free 的请求与资源限制。

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
- 第三方 X 解析接口可能故障、限流、缺失 Thread，或无法返回部分 X Articles 的完整正文；受保护、已删除及限制访问的内容不保证成功。已保存到 Notion 的旧页面不会因为升级自动补全文本；需重新归档并自行处理旧页面。
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