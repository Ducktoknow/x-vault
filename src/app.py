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
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field
from typing import Literal

from .archive import archive_post, parse_post_url
from .video_flow import inspect_post, issue_ticket, claim_ticket
from .notion import NotionPublisher

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
                if NOTION_TOKEN and NOTION_PARENT_PAGE_ID:
                    try:
                        def remember_created_page(page_url):
                            nonlocal notion_url
                            notion_url = page_url
                            with conn() as db:
                                db.execute("UPDATE archives SET notion_url=?,updated_at=? WHERE tweet_id=?",
                                           (page_url, int(time.time()), tweet_id))

                        if not notion_url:
                            notion_url, warnings = NotionPublisher(NOTION_TOKEN, NOTION_PARENT_PAGE_ID).publish(
                                archive, Path(storage) / "archives" / tweet_id,
                                "" if ephemeral else PUBLIC_BASE_URL, row["share_key"],
                                on_created=remember_created_page, ephemeral=ephemeral,
                                metadata_only=metadata_only)
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
async def shortcut_save(body: ShortcutInput):
    """One-call iOS share sheet API. Never report success until Notion confirms."""
    if not NOTION_TOKEN or not NOTION_PARENT_PAGE_ID:
        return {"status": "failed", "message": "⚠️ 尚未配置 Notion，请先在 Render 配置 Notion Token 和页面 ID"}
    match = re.search(r"https?://(?:www\.|mobile\.)?(?:x\.com|twitter\.com)/[^\s/]+/status/\d+[^\s]*", body.url, re.I)
    try:
        tweet_id, canonical = parse_post_url(match.group(0) if match else body.url)
    except ValueError as exc:
        return {"status": "failed", "message": "⚠️ 未识别到有效 X 帖子链接：" + str(exc)}
    try:
        info = await asyncio.to_thread(inspect_post, tweet_id)
    except Exception as exc:
        return {"status": "failed", "message": "⚠️ 帖子解析失败：" + str(exc)[:180]}
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


@app.get("/health")
def health():
    return {"ok": True, "service": "x-vault"}


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
        if not NOTION_TOKEN or not NOTION_PARENT_PAGE_ID:
            raise HTTPException(409, detail="尚未配置 Notion Token 和父页面，建议先下载到本地")
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
        return FileResponse(target, filename=name, media_type="application/octet-stream",
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