# X Vault

把公开的 X（Twitter）帖子、Thread、图片和视频保存到自己的空间。**个人自部署、单用户使用**，支持 iPhone 快捷指令分享收藏，以及可选的 Notion 同步。

- **iPhone 一键收藏**：从 X 分享到「收藏到 X Vault」，无需切换 Safari。收到「收藏完成」才表示 Notion 已写入；「正在处理」不代表完成。
- **图文与 Thread**：解析正文和可获取的媒体；同作者 Thread 尽力还原，并非保证抓取完整。
- **视频**：快捷收藏只保存正文、封面（若可用）和原帖链接，**不下载视频**；如需视频文件，可打开网页手动选择「下载到本机」或「保存到 Notion」。
- **网页连接 Notion**：填入一次内部连接密钥和收藏页面链接，后续快捷指令不用改。

## 1. 部署 X Vault

推荐使用 [Render](https://render.com/) Docker Web Service：

1. Fork 本项目，在 Render 新建 **Blueprint**，连接仓库的 `render.yaml`；也可以手动创建 Docker Web Service。
2. Render → Environment 设置 `APP_TOKEN`（至少 24 位随机字符）。Blueprint 若已生成，可沿用它。其他 Notion 变量可以先不填。
3. 部署后访问 `https://你的服务名.onrender.com/health`，确认返回 `"ok": true`；再打开服务首页，输入 `APP_TOKEN`。

详细步骤见 [Render 部署说明](RENDER-DEPLOY.md)。

## 2. 连接自己的 Notion

无需注册公共 OAuth 应用，也不需要 Client ID、Client Secret、回调地址。

1. 在 [Notion 我的集成](https://www.notion.so/profile/integrations) 创建**内部连接**，打开读取、插入、更新内容权限，并复制密钥。
2. 在 Notion 新建普通页面（例如「X 收藏」），在页面的「··· → 添加连接」中授权刚创建的连接访问该页，然后复制该页面链接。
3. 回到 **X Vault 首页 → 连接 Notion**，粘贴连接密钥和页面链接，点击 **验证并保存**。

网页自动提取 Page ID、验证密钥能否读取该页面，然后加密保存在服务器的 SQLite。**验证只检查读取权限；如果未开启插入权限，实际收藏时仍可能失败。** Notion 密钥不会返回浏览器，也不要把它提交到 GitHub。

**Render 免费版建议保留备用环境变量**：`NOTION_TOKEN` 和 `NOTION_PARENT_PAGE_ID`。如果网页配置与环境变量同时存在，优先使用网页配置；当免费实例丢失本地 SQLite 后，可回退到环境变量配置。不需要在 Render 修改配置才能使用网页连接功能。

## 3. 在 iPhone 的 X 中收藏

**已经能用的「收藏到 X Vault」快捷指令，无需更改或重新导入。** 新用户可以在 iOS「快捷指令」中创建一个接收共享表单「URL、文本」的快捷指令：

1. 添加 **获取 URL 内容**：地址设为 `https://你的服务名.onrender.com/api/shortcut/save`，方法 `POST`，JSON 请求正文增加 `url`，值设为蓝色变量「快捷指令输入」。
2. 请求头添加 `Authorization`，值为 `Bearer 你的APP_TOKEN`（Bearer 后有空格）。
3. 添加 **获取字典值**，从上一步响应读取键 `message`；再添加 **显示通知**，内容选择这个字典值。**不添加「打开 URL」**。

使用时，在 X 帖子中打开系统分享菜单 →「收藏到 X Vault」。服务会返回「收藏完成」「正在处理」或失败原因。Notion 尚未连接时，请打开 X Vault 网页进行设置，不用改快捷指令。

## 4. 不使用 Render：Docker 本地部署

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

## 测试

```bash
pip install -r requirements.txt pytest httpx
pytest -q
node tests/share_flow.test.cjs  # 可选：网页版分享流程测试
```

测试使用模拟数据；真正上线后还需用自己的 Render 服务、Notion 页面和公开 X 帖子进行一次端到端测试。