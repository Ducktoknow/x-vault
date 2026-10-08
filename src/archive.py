"""Public X post+thread archive using FxTwitter, with best-available video downloads.
The system stores actual downloaded files, rather than trusting mutable media links.
"""
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import requests

X_DOMAINS = {"x.com", "www.x.com", "mobile.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"}
USER_AGENT = "XV-Archive/1.0 (personal archival)"


def parse_post_url(url: str) -> tuple[str, str]:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or (parsed.hostname or "").lower() not in X_DOMAINS:
        raise ValueError("只接受 x.com 或 twitter.com 的公开帖子链接")
    match = re.fullmatch(r"/([^/]+)/status/(\d+)(?:/.*)?", parsed.path.rstrip("/"))
    if not match:
        raise ValueError("链接中缺少 /用户/status/数字格式的帖子 ID")
    return match.group(2), f"https://x.com/i/status/{match.group(2)}"


def normalize_thread(payload: dict) -> tuple[list[dict], bool]:
    """Return ordered available posts; completeness is explicitly not guaranteed."""
    focal = payload.get("status")
    candidates = (payload.get("thread") or []) + ([focal] if focal else [])
    result = {}
    for item in candidates:
        if isinstance(item, dict) and item.get("id") and item.get("type", "status") == "status":
            result[str(item["id"])] = item
    ordered = sorted(result.values(), key=lambda p: (float(p.get("created_timestamp") or 0), str(p.get("id"))))
    return ordered, isinstance(payload.get("thread"), list)


def fetch_thread(tweet_id: str, *, session=None):
    s = session or requests
    url = f"https://api.fxtwitter.com/2/thread/{tweet_id}"
    resp = s.get(url, timeout=35, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("code") not in (None, 200):
        raise RuntimeError(f"FxTwitter: {payload.get('message') or payload.get('code')}")
    posts, has_thread = normalize_thread(payload)
    if not posts:
        raise RuntimeError("没有获得可归档的公开帖子（可能私密、已删除或接口不可用）")
    return posts, has_thread, payload


def valid_cdn_url(raw: str) -> bool:
    try:
        u = urlparse(raw)
        host = (u.hostname or "").lower()
        return u.scheme == "https" and (host == "twimg.com" or host.endswith(".twimg.com") or host == "fxtwitter.com" or host.endswith(".fxtwitter.com"))
    except (ValueError, AttributeError):
        return False


def safe_file_download(url: str, dest: Path, max_bytes: int, *, session=None):
    if not valid_cdn_url(url):
        raise ValueError("媒体地址不是受信任的 X 媒体域名")
    sess = session or requests
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(dest.suffix + ".part")
    try:
        # Redirects may be used by CDN. Validate every redirect rather than allowing SSRF.
        actual_url = url
        for _ in range(5):
            resp = sess.get(actual_url, stream=True, timeout=(15, 120), allow_redirects=False,
                            headers={"User-Agent": USER_AGENT})
            if resp.status_code not in (301, 302, 303, 307, 308):
                break
            from urllib.parse import urljoin
            redirected = urljoin(actual_url, resp.headers.get("Location", ""))
            resp.close()
            if not valid_cdn_url(redirected):
                raise ValueError("媒体被重定向到非信任域名")
            actual_url = redirected
        with resp:
            resp.raise_for_status()
            if resp.status_code != 200:
                raise RuntimeError(f"媒体下载状态 {resp.status_code}")
            ctype = (resp.headers.get("Content-Type") or "").lower().split(";")[0].strip()
            if ctype in {"text/html", "text/plain", "application/json", "application/xml"}:
                raise ValueError("媒体服务器返回的是错误页面，而不是媒体文件")
            if int(resp.headers.get("Content-Length") or 0) > max_bytes:
                raise ValueError("媒体文件超出 MAX_FILE_MB 大小限制")
            count = 0
            with partial.open("wb") as fh:
                for chunk in resp.iter_content(chunk_size=1024 * 512):
                    if chunk:
                        count += len(chunk)
                        if count > max_bytes:
                            raise ValueError("下载超过 MAX_FILE_MB 配额")
                        fh.write(chunk)
            if count == 0:
                raise ValueError("媒体文件为空")
        partial.replace(dest)
        return dest
    finally:
        partial.unlink(missing_ok=True)


def best_video_urls(video: dict):
    choices = [f for f in (video.get("formats") or []) if isinstance(f, dict) and f.get("url")]
    mp4 = [f for f in choices if f.get("container") in ("mp4", "webm") and valid_cdn_url(f["url"])]
    mp4.sort(key=lambda f: (int(f.get("height") or 0) * int(f.get("width") or 0),
                            int(f.get("bitrate") or 0)), reverse=True)
    hls = [f for f in choices if f.get("container") == "m3u8" and valid_cdn_url(f["url"])]
    fallback = video.get("url")
    if fallback and valid_cdn_url(fallback):
        if ".m3u8" in fallback.split("?")[0]:
            if fallback not in [f["url"] for f in hls]:
                hls.append({"url": fallback, "container": "m3u8"})
        elif fallback not in [f["url"] for f in mp4]:
            mp4.append({"url": fallback, "container": "mp4"})
    return mp4, hls


def download_hls(url: str, basename: Path, max_bytes: int):
    if not valid_cdn_url(url):
        raise ValueError("HLS 播放列表不在允许的媒体域名")
    from yt_dlp import YoutubeDL
    output_template = str(basename) + ".%(ext)s"
    opts = {
        "format": "bestvideo+bestaudio/best",
        "outtmpl": output_template,
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "max_filesize": max_bytes,
        "socket_timeout": 35,
        "retries": 2,
        "fragment_retries": 3,
        "overwrites": True,
    }
    try:
        with YoutubeDL(opts) as ydl:
            ydl.download([url])
        files = sorted((p for p in basename.parent.glob(basename.name + ".*")
                        if p.suffix.lower() in {".mp4", ".webm", ".mkv"}), key=lambda p: p.stat().st_size, reverse=True)
        if not files:
            raise RuntimeError("HLS 处理结束后未找到视频文件")
        if files[0].stat().st_size > max_bytes:
            raise ValueError("视频文件超过大小限制")
        return files[0]
    finally:
        for p in basename.parent.glob(basename.name + ".*.part"):
            p.unlink(missing_ok=True)


def fallback_yt_dlp(post_url: str, basename: Path, max_bytes: int):
    from yt_dlp import YoutubeDL
    # Not enabled by default unless configured. X may require authentication even for public media.
    with YoutubeDL({"format": "bestvideo+bestaudio/best", "outtmpl": str(basename) + ".%(ext)s",
                    "merge_output_format": "mp4", "noplaylist": True, "quiet": True,
                    "no_warnings": True, "max_filesize": max_bytes, "socket_timeout": 35}) as ydl:
        ydl.download([post_url])
    files = [p for p in basename.parent.glob(basename.name + ".*") if p.suffix.lower() in {".mp4", ".webm", ".mkv"}]
    if not files:
        raise RuntimeError("yt-dlp 未生成视频文件")
    found = max(files, key=lambda p: p.stat().st_size)
    if found.stat().st_size > max_bytes:
        found.unlink(missing_ok=True)
        raise ValueError("yt-dlp 文件超过大小限制")
    return found


def verify_image_file(path: Path):
    data = path.open("rb").read(16)
    if not (data.startswith(b"\xff\xd8\xff") or data.startswith(b"\x89PNG\r\n\x1a\n")
            or data[:4] in (b"GIF8", b"RIFF") or data.startswith(b"BM")
            or (len(data) >= 12 and data[4:12] in (b"ftypavif", b"ftypheic"))):
        raise ValueError("下载到的不是支持的图片文件")


def verify_video_file(path: Path):
    # Reject a 200 response that merely contains an HTML error page or an HLS playlist.
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
                             "-of", "json", str(path)], capture_output=True, text=True, timeout=40)
    if result.returncode != 0:
        raise ValueError("下载的文件不是可识别的视频")
    codecs = [s.get("codec_type") for s in __import__("json").loads(result.stdout).get("streams", [])]
    if "video" not in codecs:
        raise ValueError("下载文件没有视频轨道")


def media_items(post):
    media = post.get("media") or {}
    if isinstance(media.get("all"), list) and media["all"]:
        return media["all"]
    return (media.get("photos") or []) + (media.get("videos") or [])


def archive_post(tweet_id: str, storage: Path, max_file_mb=1024, use_ytdlp_fallback=False, *, fetcher=fetch_thread, only_videos=False, metadata_only=False):
    archive_dir = storage / "archives" / tweet_id
    media_dir = archive_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    posts, has_thread, payload = fetcher(tweet_id)
    # Quote posts are separate from the author's self-thread; save their visible
    # text and attachments too, explicitly labelled as quoted content.
    expanded = []
    seen = set()
    for post in posts:
        if str(post.get("id")) not in seen:
            expanded.append((post, False))
            seen.add(str(post.get("id")))
        quote = post.get("quote")
        if isinstance(quote, dict) and quote.get("id") and quote.get("text") is not None and str(quote["id"]) not in seen:
            expanded.append((quote, True))
            seen.add(str(quote["id"]))
    max_bytes = max_file_mb * 1024 * 1024
    errors = []
    items = []
    for i, (post, is_quote) in enumerate(expanded, 1):
        person = post.get("author") or {}
        post_data = {
            "id": str(post["id"]), "url": post.get("url") or f"https://x.com/i/status/{post['id']}",
            "text": post.get("text") or "", "created_at": post.get("created_at") or "",
            "author_name": person.get("name") or person.get("display_name") or "",
            "author_username": person.get("screen_name") or person.get("username") or "",
            "is_quote": is_quote, "media": [],
        }
        for j, medium in enumerate(media_items(post), 1):
            if not isinstance(medium, dict):
                continue
            mtype = medium.get("type") or "unknown"
            if mtype in ("photo", "mosaic_photo"):
                if only_videos:
                    continue
                source = medium.get("url") or ""
                ext = (medium.get("format") or "jpg").lower().lstrip(".")
                if ext not in {"png", "jpg", "jpeg", "gif", "webp"}:
                    ext = "jpg"
                out = media_dir / f"{i:03d}-{j:02d}-image.{ext}"
                record = {"kind": "image", "source": source if valid_cdn_url(source) else "", "file": None, "alt": medium.get("altText") or ""}
                if metadata_only:
                    post_data["media"].append(record)
                    continue
                try:
                    safe_file_download(source, out, max_bytes)
                    try:
                        verify_image_file(out)
                    except Exception:
                        out.unlink(missing_ok=True)
                        raise
                    record["file"] = str(out.relative_to(archive_dir))
                except Exception as exc:
                    errors.append(f"图片 {i}-{j}: {exc}")
                post_data["media"].append(record)
            elif mtype in ("video", "gif"):
                choices, hls = best_video_urls(medium)
                base = media_dir / f"{i:03d}-{j:02d}-video"
                thumbnail = medium.get("thumbnail_url") or medium.get("poster") or ""
                record = {"kind": "video", "file": None,
                          "source": medium.get("url") if valid_cdn_url(medium.get("url") or "") else "",
                          "thumbnail_url": thumbnail if valid_cdn_url(thumbnail) else "",
                          "width": medium.get("width"), "height": medium.get("height"),
                          "duration": medium.get("duration"), "download_method": None}
                if metadata_only:
                    post_data["media"].append(record)
                    continue
                failures = []
                for choice in choices:
                    ext = ".webm" if choice.get("container") == "webm" else ".mp4"
                    try:
                        found = safe_file_download(choice["url"], base.with_suffix(ext), max_bytes)
                        try:
                            verify_video_file(found)
                        except Exception:
                            found.unlink(missing_ok=True)
                            raise
                        record.update(file=str(found.relative_to(archive_dir)), source=choice["url"], download_method="direct")
                        break
                    except Exception as exc:
                        failures.append(str(exc))
                if not record["file"]:
                    for choice in hls:
                        try:
                            found = download_hls(choice["url"], base, max_bytes)
                            try:
                                verify_video_file(found)
                            except Exception:
                                found.unlink(missing_ok=True)
                                raise
                            record.update(file=str(found.relative_to(archive_dir)), source=choice["url"], download_method="HLS/ffmpeg")
                            break
                        except Exception as exc:
                            failures.append(str(exc))
                if not record["file"] and use_ytdlp_fallback:
                    try:
                        found = fallback_yt_dlp(post_data["url"], base, max_bytes)
                        try:
                            verify_video_file(found)
                        except Exception:
                            found.unlink(missing_ok=True)
                            raise
                        record.update(file=str(found.relative_to(archive_dir)), source=post_data["url"], download_method="yt-dlp")
                    except Exception as exc:
                        failures.append(str(exc))
                if not record["file"]:
                    errors.append(f"视频 {i}-{j} 下载失败：{(' | '.join(failures) or '没有可用的视频文件地址')[:450]}")
                post_data["media"].append(record)
        items.append(post_data)
    archive = {"tweet_id": tweet_id, "source": f"https://x.com/i/status/{tweet_id}",
               "thread_returned": has_thread, "thread_completeness": "unverified",
               "posts": items, "errors": errors}
    (archive_dir / "archive.json").write_text(json.dumps(archive, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# X 内容归档 {tweet_id}", "", f"原始链接：{archive['source']}", "",
             "> 注意：Thread 完整性无法从第三方接口得到保证。", ""]
    for i, post in enumerate(items, 1):
        lines += [f"## {i}. {'引用帖：' if post.get('is_quote') else ''}@{post['author_username']}", "", post["text"], "", post["url"], ""]
        for m in post["media"]:
            if m["file"]:
                lines.append(f"![图片]({m['file']})" if m["kind"] == "image" else f"[本地视频]({m['file']})")
            elif metadata_only:
                if m["kind"] == "video":
                    lines.append(f"[在 X 查看视频]({post['url']})")
                    if m.get("thumbnail_url"):
                        lines.append(f"![视频封面]({m['thumbnail_url']})")
                elif m.get("source"):
                    lines.append(f"![图片]({m['source']})")
            else:
                lines.append(f"⚠️ {m['kind']} 未能下载")
        lines.append("")
    if errors:
        lines.extend(["## 下载问题", ""] + [f"- {x}" for x in errors])
    (archive_dir / "archive.md").write_text("\n".join(lines), encoding="utf-8")
    return archive