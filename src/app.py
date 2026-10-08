"""Single-user X Vault web API and persistent background download queue."""
import asyncio
import hmac
import json
import os
import re
import shutil
import tempfile
import zipfile
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
import secrets
import sqlite3
import time

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field
from typing import Literal

from .archive import archive_post, parse_post_url
from .video_flow import inspect_post, issue_ticket, claim_ticket
from .notion import NotionPublisher
from . import notion_config
from . import tiktok as tiktok_video

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
APP_TOKEN = os.getenv("APP_TOKEN", "")
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
NOTION_PARENT_PAGE_ID = os.getenv("NOTION_PARENT_PAGE_ID", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
MAX_FILE_MB = max(1, min(8192, int(os.getenv("MAX_FILE_MB", "1024"))))
SHORTCUT_WAIT_SECONDS = 25
DB = DATA_DIR / "vault.sqlite3"
TEMP_DIR = Path(tempfile.gettempdir()) / "xvault-transfers"
TEMP_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)


def cleanup_stale_transfers(max_age_seconds=7200):
    for path in TEMP_DIR.glob("transfer-*"):
        try:
            if path.is_dir() and time.time() - path.stat().st_mtime > max_age_seconds:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass


def conn():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=30000")
    return c


def init_db():
    with conn() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS archives (
           tweet_id TEXT PRIMARY KEY, original_url TEXT NOT NULL, status TEXT NOT NULL,
           share_key TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
           notion_url TEXT, error TEXT, warnings TEXT DEFAULT '[]')""")
        columns = {r[1] for r in db.execute("PRAGMA table_info(archives)")}
        if "delivery" not in columns:
            db.execute("ALTER TABLE archives ADD COLUMN delivery TEXT NOT NULL DEFAULT 'archive'")
        db.execute("UPDATE archives SET status='queued' WHERE status='running'")
        notion_config.init_tables(db)


def row_dict(r):
    if r is None:
        return None
    d = dict(r)
    d["warnings"] = json.loads(d.get("warnings") or "[]")
    d.pop("share_key", None)
    return d


def require_auth(request: Request):
    bearer = request.headers.get("Authorization", "")
    if not APP_TOKEN or not hmac.compare_digest(bearer, "Bearer " + APP_TOKEN):
        raise HTTPException(status_code=401, detail="请先填写个人 API Token")


def notion_credentials():
    """Resolve saved single-user Notion token; legacy env settings still work."""
    with conn() as db:
        saved = notion_config.read_settings(db)
        if saved:
            token = notion_config.decrypt_token(APP_TOKEN, saved["token_encrypted"])
            return token, saved["page_id"], "saved"
    if NOTION_TOKEN and NOTION_PARENT_PAGE_ID:
        return NOTION_TOKEN, NOTION_PARENT_PAGE_ID, "manual"
    return None, None, "none"


def process_one(tweet_id):
    with conn() as db:
        db.execute("UPDATE archives SET status='running',updated_at=?,error=NULL WHERE tweet_id=?",
                   (int(time.time()), tweet_id))
        row = db.execute("SELECT * FROM archives WHERE tweet_id=?", (tweet_id,)).fetchone()
    messages = []
    notion_url = row["notion_url"]
    metadata_only = row["delivery"] == "notion_metadata"
    ephemeral = row["delivery"] == "notion_video"
    try:
        if ephemeral and notion_url:
            # A page can be created before an upload fails. Never silently create a duplicate.
            messages.append("Notion 页面已创建；请检查页面是否完整。如需视频，请重新选择下载到本机。")
        else:
            staging = tempfile.TemporaryDirectory(prefix="notion-", dir=TEMP_DIR) if ephemeral else nullcontext(str(DATA_DIR))
            with staging as storage:
                archive = archive_post(tweet_id, Path(storage), max_file_mb=MAX_FILE_MB,
                                       use_ytdlp_fallback=os.getenv("YTDLP_X_FALLBACK", "true").lower() == "true",
                                       metadata_only=metadata_only)
                messages += archive.get("errors") or []
                notion_token, notion_parent, notion_mode = notion_credentials()
                if notion_token and notion_parent:
                    try:
                        def remember_created_page(page_url):
                            nonlocal notion_url
                            notion_url = page_url
                            with conn() as db:
                                db.execute("UPDATE archives SET notion_url=?,updated_at=? WHERE tweet_id=?",
                                           (page_url, int(time.time()), tweet_id))

                        if not notion_url:
                            def send_to_notion(access_token):
                                return NotionPublisher(access_token, notion_parent).publish(
                                    archive, Path(storage) / "archives" / tweet_id,
                                    "" if ephemeral else PUBLIC_BASE_URL, row["share_key"],
                                    on_created=remember_created_page, ephemeral=ephemeral,
                                    metadata_only=metadata_only)
                            notion_url, warnings = send_to_notion(notion_token)
                            messages += warnings
                    except Exception as exc:
                        messages.append("Notion 同步异常：" + str(exc))
                elif ephemeral:
                    messages.append("Notion 未配置，无法保存视频")
                if ephemeral:
                    # Keep searchable text+status only. No downloaded media stays in the durable volume.
                    archive_dir = DATA_DIR / "archives" / tweet_id
                    archive_dir.mkdir(parents=True, exist_ok=True)
                    for post in archive["posts"]:
                        for medium in post["media"]:
                            medium["file"] = None
                    (archive_dir / "archive.json").write_text(
                        json.dumps(archive, ensure_ascii=False, indent=2), encoding="utf-8")
        status = "partial" if messages else "complete"
        with conn() as db:
            db.execute("UPDATE archives SET status=?,notion_url=?,warnings=?,error=NULL,updated_at=? WHERE tweet_id=?",
                       (status, notion_url, json.dumps(messages, ensure_ascii=False), int(time.time()), tweet_id))
    except Exception as exc:
        with conn() as db:
            db.execute("UPDATE archives SET status='failed',error=?,updated_at=? WHERE tweet_id=?",
                       (str(exc)[:1200], int(time.time()), tweet_id))


async def queue_worker():
    while True:
        try:
            with conn() as db:
                row = db.execute("SELECT tweet_id FROM archives WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if row:
                await asyncio.to_thread(process_one, row["tweet_id"])
            else:
                await asyncio.sleep(1.5)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(_app):
    if not APP_TOKEN or len(APP_TOKEN) < 24 or APP_TOKEN == "replace-with-long-random-secret":
        raise RuntimeError("必须在 .env 配置至少24字符的 APP_TOKEN")
    init_db()
    cleanup_stale_transfers(max_age_seconds=0)
    task = asyncio.create_task(queue_worker())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="X Vault", docs_url=None, redoc_url=None, lifespan=lifespan)


class SaveInput(BaseModel):
    url: str = Field(min_length=15, max_length=2048)
    destination: Literal["local", "notion"] | None = None


class ShortcutInput(BaseModel):
    url: str = Field(min_length=15, max_length=3000)
    # New shortcut opts into direct iPhone video downloads. The original
    # shortcut (which omits this field) keeps its established Notion behavior.
    video_action: Literal["notion", "download"] = "notion"


class TikTokInput(BaseModel):
    url: str = Field(min_length=12, max_length=3500)


def shortcut_result(row):
    status = row["status"]
    notion_url = row["notion_url"]
    if status == "complete" and notion_url:
        message = "✅ 收藏完成，已保存到 Notion"
        result_status = "complete"
    elif status in ("failed", "partial") or (status == "complete" and not notion_url):
        reason = row["error"] or "；".join(json.loads(row["warnings"] or "[]")) or "未确认 Notion 保存成功"
        message = "⚠️ 收藏未完全成功：" + reason[:250]
        result_status = "partial" if status == "partial" else "failed"
    else:
        message = "⏳ 正在保存，尚未确认成功；可稍后到 X Vault 查看结果"
        result_status = "processing"
    return {"tweet_id": row["tweet_id"], "status": result_status,
            "message": message, "notion_url": notion_url}


@app.post("/api/shortcut/save", dependencies=[Depends(require_auth)])
async def shortcut_save(body: ShortcutInput, request: Request):
    """iOS share sheet: video download is opt-in; legacy Notion stays intact."""
    if "tiktok.com" in body.url.lower():
        if body.video_action != "download":
            return {"status": "failed", "message": "⚠️ TikTok 请使用『视频存本机』快捷指令，或打开 X Vault 网页下载"}
        try:
            return await prepare_tiktok_video(body.url, request)
        except HTTPException as exc:
            return {"status": "failed", "message": "⚠️ TikTok 解析失败：" + str(exc.detail)[:220]}
    match = re.search(r"https?://(?:www\.|mobile\.)?(?:x\.com|twitter\.com)/[^\s/]+/status/\d+[^\s]*", body.url, re.I)
    try:
        tweet_id, canonical = parse_post_url(match.group(0) if match else body.url)
    except ValueError as exc:
        return {"status": "failed", "message": "⚠️ 未识别到有效 X 帖子链接：" + str(exc)}
    # Older shortcuts keep their Notion behavior and should still get the
    # setup message without making an unnecessary request to X.
    if body.video_action == "notion":
        notion_token, notion_parent, _ = notion_credentials()
        if not notion_token or not notion_parent:
            setup_url = str(request.url_for("notion_setup"))
            if request.headers.get("x-forwarded-proto", "").lower() == "https" and setup_url.startswith("http://"):
                setup_url = "https://" + setup_url[len("http://"):]
            return {"status": "setup_required", "message": "尚未连接 Notion，请先在 X Vault 网页连接 Notion",
                    "setup_url": setup_url}
    try:
        info = await asyncio.to_thread(inspect_post, tweet_id)
    except Exception as exc:
        return {"status": "failed", "message": "⚠️ 帖子解析失败：" + str(exc)[:180]}
    if info["has_video"] and body.video_action == "download":
        # Temporary single-use link lets Shortcuts fetch the actual file and
        # save it on the iPhone; a server response cannot save a phone file.
        try:
            ticket = issue_ticket(tweet_id)
        except RuntimeError as exc:
            return {"status": "failed", "message": "⚠️ 暂时无法下载视频：" + str(exc)}
        download_url = str(request.url_for("download_selected_video", ticket=ticket))
        if request.headers.get("x-forwarded-proto", "").lower() == "https" and download_url.startswith("http://"):
            download_url = "https://" + download_url[len("http://"):]
        return {"tweet_id": tweet_id, "status": "download_ready",
                "download_url": download_url,
                "message": "🎬 已识别视频，正在交给快捷指令下载；文件保存前不算成功"}

    # Text / image posts, plus older shortcuts that still select Notion,
    # continue to use the existing queue and strict completion status.
    notion_token, notion_parent, _ = notion_credentials()
    if not notion_token or not notion_parent:
        setup_url = str(request.url_for("notion_setup"))
        if request.headers.get("x-forwarded-proto", "").lower() == "https" and setup_url.startswith("http://"):
            setup_url = "https://" + setup_url[len("http://"):]
        return {"status": "setup_required", "message": "尚未连接 Notion，请先在 X Vault 网页连接 Notion",
                "setup_url": setup_url}
    delivery = "notion_metadata" if info["has_video"] else "archive"
    now = int(time.time())
    with conn() as db:
        row = db.execute("SELECT * FROM archives WHERE tweet_id=?", (tweet_id,)).fetchone()
        if row is None:
            db.execute("INSERT INTO archives(tweet_id,original_url,status,share_key,created_at,updated_at,delivery)"
                       " VALUES (?,?,?,?,?,?,?)", (tweet_id, canonical, "queued", secrets.token_urlsafe(32), now, now, delivery))
        elif row["delivery"] != delivery:
            if row["notion_url"] and row["status"] == "complete":
                return shortcut_result(row)
            return {"tweet_id": tweet_id, "status": "failed",
                    "message": "⚠️ 这条帖子已有其他保存方式的记录，请在 X Vault 网页中检查"}
    # A Shortcut may time out on a cold Render instance. Bound waiting; return
    # processing, never a false positive, if the worker is still running.
    deadline = time.monotonic() + SHORTCUT_WAIT_SECONDS
    while True:
        with conn() as db:
            current = db.execute("SELECT * FROM archives WHERE tweet_id=?", (tweet_id,)).fetchone()
        if current["status"] in ("complete", "failed", "partial"):
            return shortcut_result(current)
        if time.monotonic() >= deadline:
            return shortcut_result(current)
        await asyncio.sleep(1)


async def prepare_tiktok_video(text: str, request: Request):
    """Shared TikTok preparation for browser and newer iPhone shortcuts."""
    try:
        safe_url = tiktok_video.parse_tiktok_url(text)
    except tiktok_video.TikTokError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    try:
        preview = await asyncio.to_thread(tiktok_video.inspect_tiktok, safe_url)
        ticket = tiktok_video.issue_tiktok_ticket(safe_url)
    except tiktok_video.TikTokError as exc:
        raise HTTPException(502, detail=str(exc)) from exc
    download_url = str(request.url_for("download_tiktok_video", ticket=ticket))
    if request.headers.get("x-forwarded-proto", "").lower() == "https" and download_url.startswith("http://"):
        download_url = "https://" + download_url[len("http://"):]
    return {"status": "download_ready", "preview": preview,
            "download_url": download_url,
            "message": "TikTok 解析完成，点击下载并保存到本机（未保存前不算成功）"}


@app.post("/api/tiktok/prepare", dependencies=[Depends(require_auth)])
async def tiktok_prepare(body: TikTokInput, request: Request):
    """Parse one public TikTok video and create a short-lived download URL."""
    return await prepare_tiktok_video(body.url, request)


@app.get("/api/tiktok/download/{ticket}")
def download_tiktok_video(ticket: str):
    """Consume a one-use download capability; clean up after the response."""
    video_url = tiktok_video.claim_tiktok_ticket(ticket)
    if video_url is None:
        raise HTTPException(404, detail="TikTok 下载链接已使用或超过五分钟，请重新解析")
    cleanup_stale_transfers()
    work = Path(tempfile.mkdtemp(prefix="transfer-tiktok-", dir=TEMP_DIR))
    try:
        try:
            target = tiktok_video.download_tiktok(video_url, work, max_file_mb=MAX_FILE_MB)
        except tiktok_video.TikTokError as exc:
            raise HTTPException(502, detail=str(exc)) from exc
        return FileResponse(target, filename=target.name,
                            media_type=tiktok_video.content_type(target),
                            headers={"Cache-Control": "private, no-store",
                                     "X-Content-Type-Options": "nosniff"},
                            background=BackgroundTask(shutil.rmtree, work, ignore_errors=True))
    except Exception:
        shutil.rmtree(work, ignore_errors=True)
        raise


@app.get("/api/notion/connection", dependencies=[Depends(require_auth)])
def notion_connection_status():
    """Return setup status, never the user's Notion token."""
    with conn() as db:
        saved = notion_config.read_settings(db)
    if saved:
        return {"connected": True, "mode": "saved", "page_title": saved["page_title"]}
    if NOTION_TOKEN and NOTION_PARENT_PAGE_ID:
        return {"connected": True, "mode": "environment", "page_title": "已通过环境变量配置"}
    return {"connected": False, "mode": "none", "page_title": ""}


class NotionSettingsInput(BaseModel):
    token: str = Field(min_length=8, max_length=3000)
    page_url: str = Field(min_length=24, max_length=3000)


@app.post("/api/notion/configure", dependencies=[Depends(require_auth)])
def notion_configure(body: NotionSettingsInput):
    """Validate Notion permissions before encrypting and saving settings."""
    try:
        page_id = notion_config.parse_page_id(body.page_url)
        title = notion_config.validate_page(body.token.strip(), page_id)
        with conn() as db:
            notion_config.save_settings(db, APP_TOKEN, body.token.strip(), page_id, title)
    except notion_config.NotionSetupError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    return {"connected": True, "page_title": title, "message": "Notion 已连接，之后可以直接从 X 分享收藏"}


@app.post("/api/notion/disconnect", dependencies=[Depends(require_auth)])
def notion_disconnect():
    with conn() as db:
        db.execute("DELETE FROM notion_settings WHERE id=1")
    if NOTION_TOKEN and NOTION_PARENT_PAGE_ID:
        return {"connected": True, "message": "已清除网页保存的连接；仍检测到 Render 环境变量中的 Notion 配置"}
    return {"connected": False, "message": "已断开 Notion 连接"}


@app.get("/setup/notion")
def notion_setup():
    """Public, credential-free instructions; configuration stays in Render."""
    return FileResponse(WEB_DIR / "notion-setup.html", media_type="text/html",
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@app.get("/health")
def health():
    return {"ok": True, "service": "x-vault"}


@app.head("/health", include_in_schema=False)
def health_head():
    """UptimeRobot free HTTP monitors send HEAD, not GET."""
    return Response(status_code=200)


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html", media_type="text/html")


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(WEB_DIR / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return FileResponse(WEB_DIR / "sw.js", media_type="application/javascript",
                        headers={"Service-Worker-Allowed": "/"})


@app.get("/icon.svg")
def icon():
    return FileResponse(WEB_DIR / "icon.svg", media_type="image/svg+xml")


@app.get("/api/archives", dependencies=[Depends(require_auth)])
def all_archives():
    with conn() as db:
        rows = db.execute("SELECT * FROM archives ORDER BY created_at DESC LIMIT 500").fetchall()
    return {"items": [row_dict(r) for r in rows]}


@app.get("/api/archives/{tweet_id}", dependencies=[Depends(require_auth)])
def one_archive(tweet_id: str):
    if not tweet_id.isdigit():
        raise HTTPException(400)
    with conn() as db:
        row = db.execute("SELECT * FROM archives WHERE tweet_id=?", (tweet_id,)).fetchone()
    if not row:
        raise HTTPException(404)
    doc = DATA_DIR / "archives" / tweet_id / "archive.json"
    return {"job": row_dict(row), "archive": json.loads(doc.read_text(encoding="utf-8")) if doc.is_file() else None}


@app.post("/api/save", dependencies=[Depends(require_auth)])
def save(body: SaveInput):
    try:
        tweet_id, url = parse_post_url(body.url)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))

    # A legacy shortcut calling /api/save without a destination must never
    # silently download and store a video.
    try:
        info = inspect_post(tweet_id)
    except Exception as exc:
        raise HTTPException(502, detail="X 帖子解析失败：" + str(exc)[:250])
    if info["has_video"] and body.destination is None:
        return {"status": "needs_choice", "preview": info, "url": url}
    if body.destination == "local":
        if not info["has_video"]:
            raise HTTPException(400, detail="帖子中未检测到视频")
        try:
            ticket = issue_ticket(tweet_id)
        except RuntimeError as exc:
            raise HTTPException(429, detail=str(exc))
        return {"status": "download_ready", "download_url": f"/api/download/{ticket}",
                "message": "下载链接五分钟内有效，仅能使用一次"}
    if body.destination == "notion":
        if not info["has_video"]:
            raise HTTPException(400, detail="帖子中未检测到视频")
        notion_token, notion_parent, _ = notion_credentials()
        if not notion_token or not notion_parent:
            raise HTTPException(409, detail="Notion 尚未连接或未选择收藏页面")
    destination = "notion_video" if body.destination == "notion" else "archive"
    now = int(time.time())
    with conn() as db:
        row = db.execute("SELECT * FROM archives WHERE tweet_id=?", (tweet_id,)).fetchone()
        if not row:
            db.execute("INSERT INTO archives(tweet_id,original_url,status,share_key,created_at,updated_at,delivery)"
                       " VALUES (?,?,?,?,?,?,?)",
                       (tweet_id, url, "queued", secrets.token_urlsafe(32), now, now, destination))
            return {"tweet_id": tweet_id, "status": "queued", "new": True, "delivery": destination}
        if row["delivery"] != destination:
            raise HTTPException(409, detail="已有不同保存方式的归档记录；可直接选择本机下载，不会增加服务器存储")
        return {"tweet_id": tweet_id, "status": row["status"], "new": False,
                "delivery": row["delivery"], "message": "已存在归档记录"}


@app.get("/api/download/{ticket}")
def download_selected_video(ticket: str):
    tweet_id = claim_ticket(ticket)
    if tweet_id is None:
        raise HTTPException(404, detail="下载链接已使用或过期，请重新解析")
    cleanup_stale_transfers()
    work = Path(tempfile.mkdtemp(prefix="transfer-", dir=TEMP_DIR))
    try:
        archive = archive_post(tweet_id, work, max_file_mb=MAX_FILE_MB,
                               use_ytdlp_fallback=os.getenv("YTDLP_X_FALLBACK", "true").lower() == "true",
                               only_videos=True)
        base = work / "archives" / tweet_id
        downloaded = [base / m["file"] for post in archive["posts"]
                      for m in post["media"] if m.get("kind") == "video" and m.get("file")]
        if not downloaded:
            raise HTTPException(502, detail="视频提取失败：" + "；".join(archive["errors"])[:350])
        # Single video: a genuine video attachment. Multiple videos or partial
        # failure: ZIP with source text, metadata, all files and failure messages.
        if len(downloaded) == 1 and not archive["errors"]:
            target = downloaded[0]
            name = f"x-{tweet_id}{target.suffix}"
        else:
            target = work / f"x-{tweet_id}.zip"
            with zipfile.ZipFile(target, "w", zipfile.ZIP_STORED) as zf:
                for file in [base / "archive.json", base / "archive.md"] + downloaded:
                    zf.write(file, file.relative_to(base))
            name = target.name
        # Accurate MIME types help iOS Shortcuts retain MP4/WebM/ZIP file
        # identities when saving downloaded content to the Files app.
        content_type = {".mp4": "video/mp4", ".webm": "video/webm",
                        ".zip": "application/zip"}.get(target.suffix.lower(), "application/octet-stream")
        return FileResponse(target, filename=name, media_type=content_type,
                            headers={"Cache-Control": "private, no-store",
                                     "X-Content-Type-Options": "nosniff"},
                            background=BackgroundTask(shutil.rmtree, work, ignore_errors=True))
    except Exception:
        shutil.rmtree(work, ignore_errors=True)
        raise


@app.post("/api/retry/{tweet_id}", dependencies=[Depends(require_auth)])
def retry(tweet_id: str):
    with conn() as db:
        row = db.execute("SELECT status,delivery,notion_url FROM archives WHERE tweet_id=?", (tweet_id,)).fetchone()
        if not row:
            raise HTTPException(404)
        if row["status"] == "running":
            return {"status": "running"}
        if row["delivery"] in ("notion_video", "notion_metadata") and row["notion_url"]:
            raise HTTPException(409, detail="Notion 页面已创建，不能自动重试覆盖；请检查页面或改用本机下载")
        db.execute("UPDATE archives SET status='queued',error=NULL,updated_at=? WHERE tweet_id=?", (int(time.time()), tweet_id))
    return {"status": "queued"}


@app.get("/media/{tweet_id}/{filename}")
def media(tweet_id: str, filename: str, key: str = ""):
    if not tweet_id.isdigit() or filename in {".", ".."} or "/" in filename or "\\" in filename:
        raise HTTPException(404)
    with conn() as db:
        row = db.execute("SELECT share_key FROM archives WHERE tweet_id=?", (tweet_id,)).fetchone()
    if not row or not key or not hmac.compare_digest(key, row["share_key"]):
        raise HTTPException(404)
    parent = DATA_DIR / "archives" / tweet_id
    index_file = parent / "archive.json"
    if not index_file.exists():
        raise HTTPException(404)
    data = json.loads(index_file.read_text(encoding="utf-8"))
    valid_files = {Path(m["file"]).name for p in data.get("posts", []) for m in p.get("media", []) if m.get("file")}
    if filename not in valid_files:
        raise HTTPException(404)
    target = parent / "media" / filename
    if not target.is_file():
        raise HTTPException(404)
    # Capability URL with unguessable 256-bit share_key; share selectively.
    return FileResponse(target, filename=filename, media_type="application/octet-stream",
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@app.get("/api/media-link/{tweet_id}/{filename}", dependencies=[Depends(require_auth)])
def media_link(tweet_id: str, filename: str, request: Request):
    if not tweet_id.isdigit() or filename in {".", ".."} or "/" in filename or "\\" in filename:
        raise HTTPException(404)
    with conn() as db:
        row = db.execute("SELECT share_key FROM archives WHERE tweet_id=?", (tweet_id,)).fetchone()
    if not row:
        raise HTTPException(404)
    from urllib.parse import quote
    url = str(request.base_url).rstrip("/") + f"/media/{tweet_id}/" + quote(filename) + "?key=" + row["share_key"]
    return {"url": url}


@app.get("/api/media/{tweet_id}/{filename}", dependencies=[Depends(require_auth)])
def auth_media(tweet_id: str, filename: str):
    if not tweet_id.isdigit() or filename in {".", ".."} or "/" in filename or "\\" in filename:
        raise HTTPException(404)
    parent = DATA_DIR / "archives" / tweet_id
    doc = parent / "archive.json"
    if not doc.exists():
        raise HTTPException(404)
    data = json.loads(doc.read_text(encoding="utf-8"))
    valid_files = {Path(m["file"]).name for p in data.get("posts", []) for m in p.get("media", []) if m.get("file")}
    target = parent / "media" / filename
    if filename not in valid_files or not target.is_file():
        raise HTTPException(404)
    return FileResponse(target, filename=filename, media_type="application/octet-stream")