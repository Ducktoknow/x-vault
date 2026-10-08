"""Public TikTok video previews and temporary, one-use downloads.

Only recognized TikTok video links reach yt-dlp. No login/cookies, no
playlists, no database writes, and downloaded files are removed after transfer.
"""
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

import yt_dlp
from yt_dlp.utils import DownloadError


LINK_RE = re.compile(r"https?://[^\s<>\"'“”]+", re.IGNORECASE)
VIDEO_PATH = re.compile(
    r"^/(?:@[^/]+/video/\d+|video/\d+|share/video/\d+|t/[A-Za-z0-9_-]+)(?:/)?$",
    re.IGNORECASE)
SHORT_PATH = re.compile(r"^/[A-Za-z0-9_-]+/?$")
PUBLIC_HOSTS = {"tiktok.com", "www.tiktok.com", "m.tiktok.com",
                "vm.tiktok.com", "vt.tiktok.com"}

_TICKETS = {}
_TICKET_LOCK = threading.Lock()
_DOWNLOAD_SLOTS = threading.BoundedSemaphore(2)
_TTL_SECONDS = 300
_MAX_TICKETS = 100
_VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv", ".m4v"}
_VIDEO_TYPES = {".mp4": "video/mp4", ".webm": "video/webm",
                ".mov": "video/quicktime", ".m4v": "video/x-m4v",
                ".mkv": "video/x-matroska"}


class TikTokError(ValueError):
    pass


def parse_tiktok_url(text):
    """Find a real, public TikTok *video* link in app share text.

    Strip tracking/query parameters so they are not exposed in output or logs.
    Do not follow arbitrary redirects or allow user-supplied download URLs.
    """
    text = str(text).strip()
    if len(text) > 3500:
        raise TikTokError("分享内容过长，请直接粘贴 TikTok 视频链接")
    saw_tiktok = False
    for match in LINK_RE.finditer(text):
        candidate = match.group().rstrip("，。！？!;；,.)]}）>\u200b")
        try:
            parsed = urlsplit(candidate)
            hostname = (parsed.hostname or "").lower()
            if hostname not in PUBLIC_HOSTS:
                continue
            saw_tiktok = True
            if parsed.scheme.lower() != "https" or parsed.username or parsed.password or parsed.port:
                continue
            path = parsed.path
            if hostname in {"vm.tiktok.com", "vt.tiktok.com"}:
                valid = bool(SHORT_PATH.fullmatch(path))
            else:
                valid = bool(VIDEO_PATH.fullmatch(path))
            if valid:
                return f"https://{hostname}{path}"
        except ValueError:
            continue
    if saw_tiktok:
        raise TikTokError("只支持 TikTok 公开视频链接，请复制具体视频的分享链接（不支持主页、直播或图集）")
    raise TikTokError("没有识别到 TikTok 视频链接，支持 www.tiktok.com 和 vm/vt.tiktok.com 短链")


def _options(**overrides):
    data = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        # Do not use max_downloads=1: yt-dlp raises MaxDownloadsReached
        # even *after successfully downloading one video*, resulting in 500.
        # Single-video URLs are enforced by parse_tiktok_url and file checks.
        "socket_timeout": 15,
        "retries": 2, "fragment_retries": 2,
        "allowed_extractors": ["TikTok", "vm.tiktok"],
    }
    data.update(overrides)
    return data


def inspect_tiktok(url):
    """Extract safe preview fields without downloading the video."""
    try:
        with yt_dlp.YoutubeDL(_options(skip_download=True)) as ydl:
            info = ydl.extract_info(url, download=False)
    except DownloadError as exc:
        raise TikTokError("TikTok 解析失败。公开视频也可能需要登录、验证码或受地区限制：" + str(exc)[:150]) from exc
    except (OSError, ValueError) as exc:
        raise TikTokError("TikTok 视频暂时无法解析：" + str(exc)[:130]) from exc
    if not isinstance(info, dict) or info.get("_type") in ("playlist", "multi_video"):
        raise TikTokError("暂不支持 TikTok 图集、合集或直播，仅支持单条公开视频")
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
        raise TikTokError("暂不支持下载 TikTok 直播")
    video_id = re.sub(r"[^A-Za-z0-9_-]", "", str(info.get("id") or ""))[:70]
    if not video_id:
        raise TikTokError("TikTok 视频 ID 未解析成功")
    duration = info.get("duration")
    if not isinstance(duration, (int, float)) or duration < 0:
        duration = None
    return {
        "id": video_id, "title": str(info.get("title") or info.get("description") or "TikTok 视频")[:140],
        "author": str(info.get("uploader") or info.get("creator") or "")[:100],
        "duration": round(duration) if duration is not None else None,
        "thumbnail": (str(info.get("thumbnail"))[:1200]
                      if str(info.get("thumbnail") or "").startswith("https://") else None),
    }


def issue_tiktok_ticket(url):
    """One-use, random 256-bit download capability, expires in five minutes."""
    # Internal callers must not bypass URL validation.
    checked = parse_tiktok_url(url)
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _TICKET_LOCK:
        for key, (_url, expiry) in list(_TICKETS.items()):
            if expiry <= now:
                del _TICKETS[key]
        if len(_TICKETS) >= _MAX_TICKETS:
            raise TikTokError("临时下载请求太多，请稍后再试")
        _TICKETS[token] = (checked, now + _TTL_SECONDS)
    return token


def claim_tiktok_ticket(token):
    with _TICKET_LOCK:
        data = _TICKETS.pop(token, None)
    return data[0] if data and data[1] > time.time() else None


def download_tiktok(url, target_dir: Path, max_file_mb=150):
    """Download exactly one video in a short-lived private directory.

    Returns a video Path. The caller owns cleanup, including on errors.
    """
    clean_url = parse_tiktok_url(url)
    limit_bytes = max(1, min(int(max_file_mb), 150)) * 1024 * 1024
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    if not _DOWNLOAD_SLOTS.acquire(blocking=False):
        raise TikTokError("当前下载任务较多，请稍后重试")

    def check_size(progress):
        reported = progress.get("downloaded_bytes") or progress.get("total_bytes") or 0
        if reported > limit_bytes:
            raise TikTokError("文件超过当前单视频下载上限（150 MB 或服务器设置的更低限制）")

    try:
        try:
            with yt_dlp.YoutubeDL(_options(
                format="bv*+ba/b", merge_output_format="mp4",
                outtmpl=str(target_dir / "tiktok-%(id)s.%(ext)s"),
                restrictfilenames=True,
                max_filesize=limit_bytes,
                progress_hooks=[check_size],
            )) as ydl:
                ydl.extract_info(clean_url, download=True)
        except DownloadError as exc:
            raise TikTokError("TikTok 视频下载失败，可能需要登录或受到地区限制：" + str(exc)[:150]) from exc
        files = [p for p in target_dir.iterdir()
                 if p.is_file() and p.suffix.lower() in _VIDEO_SUFFIXES and not p.is_symlink()]
        if len(files) != 1:
            raise TikTokError("未找到单个可下载的视频文件；可能是图集、受限制视频或解析失败")
        path = files[0]
        if path.stat().st_size == 0:
            raise TikTokError("下载的视频文件为空")
        if path.stat().st_size > limit_bytes:
            raise TikTokError("视频超过单文件大小限制")
        return path
    finally:
        _DOWNLOAD_SLOTS.release()


def content_type(path):
    return _VIDEO_TYPES.get(Path(path).suffix.lower(), "application/octet-stream")