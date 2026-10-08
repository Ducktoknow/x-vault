"""Tests for iPhone Shortcuts, no-video-download bookmark and completion semantics."""
import importlib
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient
from src.archive import archive_post
from src.notion import NotionPublisher

TOKEN = "test-secret-token-01234567890123"
HEADERS = {"Authorization": "Bearer " + TOKEN}
POST = "https://x.com/poster/status/738291"


async def idle():
    await asyncio.sleep(300)


def test_video_metadata_only_does_not_download_anything(monkeypatch):
    video = {
        "id": "738291", "text": "Example clip",
        "author": {"screen_name": "poster"}, "media": {"all": [
            {"type": "video", "url": "https://video.twimg.com/test.mp4",
             "thumbnail_url": "https://pbs.twimg.com/ext_tw_video_thumb/123/pu/img/test.jpg"}]}
    }
    def forbidden(*args, **kwargs):
        raise AssertionError("metadata-only must not download media")
    monkeypatch.setattr("src.archive.safe_file_download", forbidden)
    monkeypatch.setattr("src.archive.download_hls", forbidden)
    monkeypatch.setattr("src.archive.fallback_yt_dlp", forbidden)
    with TemporaryDirectory() as root:
        out = archive_post("738291", Path(root), metadata_only=True,
                           fetcher=lambda _id: ([video], True, {}))
        medium = out["posts"][0]["media"][0]
        assert medium["kind"] == "video"
        assert medium["thumbnail_url"].startswith("https://pbs.twimg.com")
        assert medium["file"] is None
        assert not list((Path(root) / "archives/738291/media").iterdir())
        assert out["errors"] == []


def test_notion_metadata_contains_external_cover_and_x_link(monkeypatch):
    calls = []
    pub = NotionPublisher("test", "abc")
    pub.create_page = lambda title: ("notion_page", "https://notion.so/notion_page")
    pub.append = lambda id, blocks: calls.extend(blocks)
    archive = {
        "tweet_id": "738291", "source": POST, "errors": [],
        "posts": [{"text": "Example clip", "url": POST, "created_at": "",
                   "author_username": "poster", "media": [
                     {"kind": "video", "file": None, "thumbnail_url":
                      "https://pbs.twimg.com/ext_tw_video_thumb/test.jpg"}]}]}
    with TemporaryDirectory() as root:
        url, errors = pub.publish(archive, Path(root), metadata_only=True)
    assert url == "https://notion.so/notion_page" and errors == []
    assert any(b["type"] == "image" and b["image"]["external"]["url"].startswith("https://pbs.twimg.com") for b in calls)
    assert any(b["type"] == "paragraph" and "在 X 播放原视频" in
               b["paragraph"]["rich_text"][0]["text"]["content"] for b in calls)
    assert not any(b.get("type") == "video" for b in calls)


def test_shortcut_video_full_lifecycle_and_no_false_success(monkeypatch):
    with TemporaryDirectory() as root:
        monkeypatch.setenv("DATA_DIR", root)
        monkeypatch.setenv("APP_TOKEN", TOKEN)
        monkeypatch.setenv("NOTION_TOKEN", "fake-token")
        monkeypatch.setenv("NOTION_PARENT_PAGE_ID", "abcdef")
        import src.app as appmod
        importlib.reload(appmod)
        monkeypatch.setattr(appmod, "inspect_post", lambda id: {"has_video": True})
        monkeypatch.setattr(appmod, "SHORTCUT_WAIT_SECONDS", 0)
        video = {"id": "738291", "text": "Example", "author": {"screen_name": "poster"},
                 "media": {"all": [{"type": "video",
                                    "thumbnail_url": "https://pbs.twimg.com/cover.jpg"}]}}
        from src.archive import archive_post as real_archive
        monkeypatch.setattr(appmod, "archive_post", lambda tweet_id, storage, **kwargs: real_archive(
            tweet_id, storage, fetcher=lambda id: ([video], True, {}), **kwargs))
        class FakePublisher:
            def __init__(self, *args, **kwargs): pass
            def publish(self, archive, path, public_base_url, share_key, *,
                        on_created=None, ephemeral=False, metadata_only=False):
                assert metadata_only
                assert archive["posts"][0]["media"][0]["file"] is None
                on_created("https://notion.so/yes")
                return "https://notion.so/yes", []
        monkeypatch.setattr(appmod, "NotionPublisher", FakePublisher)
        with patch.object(appmod, "queue_worker", side_effect=idle):
            with TestClient(appmod.app) as client:
                payload = {"url": "Look at this: " + POST + " wow"}
                first = client.post("/api/shortcut/save", headers=HEADERS, json=payload)
                assert first.status_code == 200
                assert first.json()["status"] == "processing"
                assert first.json()["tweet_id"] == "738291"
                appmod.process_one("738291")
                with patch.object(appmod, "inspect_post", return_value={"has_video": True}):
                    second = client.post("/api/shortcut/save", headers=HEADERS, json=payload)
                assert second.json()["status"] == "complete"
                assert "Notion" in second.json()["message"]
                assert not (Path(root) / "archives/738291/media/001-01-video.mp4").exists()
                with appmod.conn() as db:
                    row = db.execute("SELECT delivery,notion_url FROM archives").fetchone()
                    assert row["delivery"] == "notion_metadata"


def test_shortcut_invalid_and_missing_notion(monkeypatch):
    with TemporaryDirectory() as root:
        monkeypatch.setenv("DATA_DIR", root)
        monkeypatch.setenv("APP_TOKEN", TOKEN)
        monkeypatch.setenv("NOTION_TOKEN", "")
        monkeypatch.setenv("NOTION_PARENT_PAGE_ID", "")
        import src.app as appmod
        importlib.reload(appmod)
        with patch.object(appmod, "queue_worker", side_effect=idle):
            with TestClient(appmod.app) as client:
                missing = client.post("/api/shortcut/save", headers=HEADERS,
                                      json={"url": POST})
                assert missing.json()["status"] == "failed"
                assert "Notion" in missing.json()["message"]
                assert client.post("/api/shortcut/save", json={"url": POST}).status_code == 401


def test_shortcut_not_claim_success_on_notion_failure(monkeypatch):
    with TemporaryDirectory() as root:
        monkeypatch.setenv("DATA_DIR", root)
        monkeypatch.setenv("APP_TOKEN", TOKEN)
        monkeypatch.setenv("NOTION_TOKEN", "t")
        monkeypatch.setenv("NOTION_PARENT_PAGE_ID", "p")
        import src.app as appmod
        importlib.reload(appmod)
        monkeypatch.setattr(appmod, "inspect_post", lambda id: {"has_video": True})
        monkeypatch.setattr(appmod, "SHORTCUT_WAIT_SECONDS", 0)
        with patch.object(appmod, "queue_worker", side_effect=idle):
            with TestClient(appmod.app) as client:
                first = client.post("/api/shortcut/save", headers=HEADERS, json={"url": POST})
                assert first.json()["status"] == "processing"
                with appmod.conn() as db:
                    db.execute("UPDATE archives SET status='partial', warnings=? WHERE tweet_id=?",
                               ('["Notion 上传失败"]', "738291"))
                later = client.post("/api/shortcut/save", headers=HEADERS, json={"url": POST})
                assert later.json()["status"] == "partial"
                assert "收藏完成" not in later.json()["message"]