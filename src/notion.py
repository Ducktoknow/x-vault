"""Notion page+media upload, including multipart video within workspace plan limits."""
import math
import mimetypes
from pathlib import Path
import time
import requests

from .archive import valid_cdn_url

NOTION_VERSION = "2026-03-11"
PART_SIZE = 10 * 1024 * 1024  # <20 MiB per part


def rt(s, link=None):
    obj = {"type": "text", "text": {"content": str(s)[:2000]}}
    if link:
        obj["text"]["link"] = {"url": link}
    return obj


def paragraph(text, link=None):
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [rt(text, link)]}}


def split_text(text, n=1800):
    return [text[i:i+n] for i in range(0, len(text), n)] or [""]


class NotionPublisher:
    def __init__(self, token, parent_page_id, session=None):
        self.token = token
        self.parent = parent_page_id.replace("-", "")
        self.session = session or requests.Session()
        self.max_size = None

    def call(self, method, path, **kwargs):
        hdr = {"Authorization": f"Bearer {self.token}", "Notion-Version": NOTION_VERSION}
        response = self.session.request(method, "https://api.notion.com/v1" + path,
                                        headers=hdr, timeout=120, **kwargs)
        for retry in range(2):
            if response.status_code != 429:
                break
            time.sleep(min(float(response.headers.get("Retry-After", "2")), 5))
            response = self.session.request(method, "https://api.notion.com/v1" + path,
                                            headers=hdr, timeout=120, **kwargs)
        if not response.ok:
            raise RuntimeError(f"Notion {response.status_code}: {response.text[:700]}")
        return response.json()

    def max_upload_size(self):
        if self.max_size is None:
            bot = self.call("GET", "/users/me")
            self.max_size = int((bot.get("bot") or {}).get("workspace_limits", {}).get("max_file_upload_size_in_bytes") or 5*1024*1024)
        return self.max_size

    def upload(self, path: Path):
        size = path.stat().st_size
        if size > self.max_upload_size():
            return None
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        multi = size > 20*1024*1024
        n = math.ceil(size / PART_SIZE) if multi else 1
        payload = {"mode": "multi_part" if multi else "single_part", "filename": path.name,
                   "content_type": content_type}
        if multi:
            payload["number_of_parts"] = n
        created = self.call("POST", "/file_uploads", json=payload)
        with path.open("rb") as fh:
            for i in range(n):
                if multi:
                    # Requests buffers one 10-MiB part; never the whole video.
                    data = fh.read(PART_SIZE)
                    file_value = (path.name, data, content_type)
                else:
                    file_value = (path.name, fh, content_type)
                self.call("POST", f"/file_uploads/{created['id']}/send",
                          files={"file": file_value},
                          data={"part_number": str(i+1)} if multi else {})
        if multi:
            self.call("POST", f"/file_uploads/{created['id']}/complete", json={})
        return created["id"]

    def create_page(self, title):
        page = self.call("POST", "/pages", json={
            "parent": {"type": "page_id", "page_id": self.parent},
            "properties": {"title": {"type": "title", "title": [rt(title[:95])]}}
        })
        return page["id"], page["url"]

    def append(self, page_id, blocks):
        for i in range(0, len(blocks), 50):
            self.call("PATCH", f"/blocks/{page_id}/children", json={"children": blocks[i:i+50]})

    def publish(self, archive, archive_dir, public_base_url="", share_key="", *, on_created=None, ephemeral=False, metadata_only=False):
        first = archive["posts"][0]
        title = (first.get("article_title") or first["text"].replace("\n", " ")[:75]
                 or f"X帖子 {archive['tweet_id']}").strip()
        page_id, page_url = self.create_page(f"X｜{title}")
        if on_created is not None:
            on_created(page_url)
        warnings = []
        blocks = [paragraph("原帖：" + archive["source"], archive["source"]),
                  paragraph("本收藏只包含正文、封面与原帖链接；不会下载或上传视频文件。Thread 完整性不保证。" if metadata_only else
                            ("Thread 的完整性无法保证；视频临时处理中转完成后会从服务器删除。" if ephemeral else
                             "Thread 的完整性无法保证；原始内容和媒体也保存在自托管服务中。"))]
        for i, post in enumerate(archive["posts"], 1):
            blocks.append({"object": "block", "type": "heading_2", "heading_2": {
                "rich_text": [rt(f"{i}. {'引用帖 ' if post.get('is_quote') else ''}@{post.get('author_username') or 'unknown'} · {post.get('created_at') or ''}"[:180])]}})
            for part in split_text(post["text"]):
                if part:
                    blocks.append(paragraph(part))
            cover = post.get("article_cover_url") or ""
            if valid_cdn_url(cover):
                blocks.append({"object": "block", "type": "image", "image": {
                    "type": "external", "external": {"url": cover}}})
            blocks.append(paragraph("在 X 打开", post["url"]))
            for medium in post["media"]:
                if metadata_only:
                    if medium["kind"] == "video":
                        blocks.append(paragraph("▶️ 在 X 播放原视频", post["url"]))
                        candidates = medium.get("direct_urls") or []
                        for number, video_url in enumerate(candidates[:3], 1):
                            if valid_cdn_url(video_url) and len(video_url) <= 1900:
                                blocks.append(paragraph(f"视频解析直链 {number}（可能过期）", video_url))
                        if candidates:
                            blocks.append(paragraph("⚠️ 上面是视频 CDN 的临时地址，可能过期或需要平台请求头；失效后可用原帖重新解析下载。"))
                        thumbnail = medium.get("thumbnail_url") or ""
                        if valid_cdn_url(thumbnail):
                            blocks.append({"object": "block", "type": "image", "image": {
                                "type": "external", "external": {"url": thumbnail}}})
                        else:
                            blocks.append(paragraph("视频封面暂不可用，可通过原帖链接观看。"))
                    elif medium["kind"] == "image" and valid_cdn_url(medium.get("source") or ""):
                        blocks.append({"object": "block", "type": "image", "image": {
                            "type": "external", "external": {"url": medium["source"]}}})
                    continue
                file_name = medium.get("file")
                if not file_name:
                    blocks.append(paragraph("⚠️ 媒体文件未能下载。"))
                    continue
                path = archive_dir / file_name
                if not path.is_file():
                    blocks.append(paragraph("⚠️ 本地媒体缺失。"))
                    continue
                upload_id = None
                try:
                    upload_id = self.upload(path)
                except Exception as exc:
                    warnings.append(f"Notion 上传 {path.name} 失败：{exc}")
                if upload_id:
                    medium["notion_stored"] = True
                    kind = "image" if medium["kind"] == "image" else "video"
                    # Notion itself becomes owner of media; no public link needed.
                    blocks.append({"object": "block", "type": kind,
                                   kind: {"type": "file_upload", "file_upload": {"id": upload_id}}})
                elif ephemeral:
                    blocks.append(paragraph(f"⚠️ {path.name} 未能上传至 Notion，请选择下载到本机保存。"))
                    warnings.append(f"{path.name} 未上传 Notion（可能超过账户限额）；服务器不留存视频，可重新选择本机下载")
                elif public_base_url:
                    from urllib.parse import quote
                    media_url = (public_base_url.rstrip("/") + f"/media/{archive['tweet_id']}/" +
                                 quote(path.name) + f"?key={share_key}")
                    blocks.append(paragraph(f"下载归档{'视频' if medium['kind']=='video' else '图片'}：{path.name}", media_url))
                    warnings.append(f"{path.name} 超过 Notion 文件上传额度，已链接到私有自托管归档")
                else:
                    blocks.append(paragraph(f"本地已归档 {path.name}；超过 Notion 上传限制，未生成公网文件链接"))
                    warnings.append(f"{path.name} 超过 Notion 上传额度，请通过自建服务下载")
        if archive.get("errors"):
            blocks.append({"object": "block", "type": "heading_2", "heading_2": {"rich_text": [rt("归档警告")]}})
            blocks += [paragraph(msg[:1900]) for msg in archive["errors"][:30]]
        try:
            self.append(page_id, blocks)
        except Exception as exc:
            # Surface partial page ID, so caller can avoid accidental duplication.
            raise RuntimeError(f"Notion 页面已创建但填充失败；页面地址 {page_url}；原因：{exc}") from exc
        return page_url, warnings
    def publish_tiktok(self, original_url, preview):
        """Save TikTok metadata and perishable CDN links, without media download."""
        name = str(preview.get("title") or "TikTok 视频")[:100]
        page_id, page_url = self.create_page("TikTok｜" + name)
        blocks = [paragraph("原视频：" + original_url, original_url),
                  paragraph("作者：" + str(preview.get("author") or "未知")),
                  paragraph("视频说明：" + name)]
        duration = preview.get("duration")
        if duration is not None:
            blocks.append(paragraph(f"时长：{duration} 秒"))
        image = str(preview.get("thumbnail") or "")
        # External Notion blocks require HTTPS and accessible image assets.
        if image.startswith("https://") and len(image) < 1800:
            blocks.append({"object": "block", "type": "image", "image": {
                "type": "external", "external": {"url": image}}})
        urls = preview.get("video_urls") or []
        for idx, url in enumerate(urls[:3], 1):
            if isinstance(url, str) and url.startswith("https://") and len(url) < 1900:
                blocks.append(paragraph(f"解析视频直链 {idx}（可能过期）", url))
        blocks.append(paragraph("⚠️ 视频直链包含平台签名，通常会过期，也可能要求特定请求头；请保留原视频链接，过期后重新解析下载。"))
        try:
            self.append(page_id, blocks)
        except Exception as exc:
            raise RuntimeError(f"Notion 页面已创建但内容写入失败；页面地址 {page_url}；原因：{exc}") from exc
        return page_url