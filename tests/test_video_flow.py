"""Regression tests for opt-in video delivery without persistent server media."""
import importlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import asyncio

from fastapi.testclient import TestClient

AUTH = {"Authorization": "Bearer 0123456789abcdef0123456789abcdef"}
URL = "https://x.com/test/status/888"


async def idle():
    await asyncio.sleep(300)


def fake_video_archive(tweet_id, storage, **kwargs):
    parent = Path(storage) / "archives" / tweet_id
    media = parent / "media"
    media.mkdir(parents=True, exist_ok=True)
    file = media / "001-01-video.mp4"
    file.write_bytes(b"test-video-bytes")
    archive = {"tweet_id": tweet_id, "source": f"https://x.com/i/status/{tweet_id}",
               "posts": [{"id": tweet_id, "text": "Video post", "url": URL,
                          "author_username": "test", "created_at": "",
                          "media": [{"kind": "video", "file": "media/001-01-video.mp4"}]}],
               "errors": []}
    (parent / "archive.json").write_text(json.dumps(archive))
    (parent / "archive.md").write_text("# test")
    return archive


def initialize(monkeypatch, root, *, notion=False):
    monkeypatch.setenv("DATA_DIR", str(root))
    monkeypatch.setenv("APP_TOKEN", AUTH["Authorization"].replace("Bearer ", ""))
    monkeypatch.setenv("NOTION_TOKEN", "fake-notion-token" if notion else "")
    monkeypatch.setenv("NOTION_PARENT_PAGE_ID", "abcd1234" if notion else "")
    import src.app as mod
    importlib.reload(mod)
    monkeypatch.setattr(mod, "inspect_post", lambda tweet_id: {
        "tweet_id": tweet_id, "has_video": True, "video_count": 1,
        "post_count": 1, "thread_returned": True, "text_preview": "Video post",
        "author": "test"})
    return mod


def test_video_requires_explicit_choice_and_local_download_cleans_up(monkeypatch):
    with TemporaryDirectory() as tmp:
        mod = initialize(monkeypatch, tmp)
        monkeypatch.setattr(mod, "archive_post", fake_video_archive)
        with patch.object(mod, "queue_worker", side_effect=idle):
            with TestClient(mod.app) as client:
                initial = client.post("/api/save", headers=AUTH, json={"url": URL})
                assert initial.status_code == 200
                assert initial.json()["status"] == "needs_choice"
                assert client.get("/api/archives", headers=AUTH).json()["items"] == []
                local = client.post("/api/save", headers=AUTH, json={"url": URL, "destination": "local"})
                assert local.json()["status"] == "download_ready"
                link = local.json()["download_url"]
                assert client.get(link).content == b"test-video-bytes"
                assert client.get(link).status_code == 404  # one-use link
                assert not (Path(tmp) / "archives").exists()
                assert not list(mod.TEMP_DIR.glob("transfer-*"))


def test_notion_video_uses_ephemeral_files_and_retains_text_only(monkeypatch):
    with TemporaryDirectory() as tmp:
        mod = initialize(monkeypatch, tmp, notion=True)
        monkeypatch.setattr(mod, "archive_post", fake_video_archive)
        class FakePublisher:
            def __init__(self, *args, **kwargs):
                pass
            def publish(self, archive, archive_dir, public_base_url, share_key, *,
                        on_created=None, ephemeral=False):
                assert ephemeral and not public_base_url
                assert (archive_dir / "media/001-01-video.mp4").exists()
                on_created("https://notion.so/test-page")
                archive["posts"][0]["media"][0]["notion_stored"] = True
                return "https://notion.so/test-page", []
        monkeypatch.setattr(mod, "NotionPublisher", FakePublisher)
        with patch.object(mod, "queue_worker", side_effect=idle):
            with TestClient(mod.app) as client:
                queued = client.post("/api/save", headers=AUTH,
                                     json={"url": URL, "destination": "notion"})
                assert queued.json()["status"] == "queued"
                mod.process_one("888")
                detail = client.get("/api/archives/888", headers=AUTH).json()
                assert detail["job"]["status"] == "complete"
                assert detail["job"]["notion_url"] == "https://notion.so/test-page"
                medium = detail["archive"]["posts"][0]["media"][0]
                assert medium["file"] is None and medium["notion_stored"] is True
                assert not (Path(tmp) / "archives/888/media").exists()
                assert client.post("/api/retry/888", headers=AUTH).status_code == 409


def test_local_ticket_requires_authentication_and_blocks_nonvideo(monkeypatch):
    with TemporaryDirectory() as tmp:
        mod = initialize(monkeypatch, tmp)
        with patch.object(mod, "queue_worker", side_effect=idle):
            with TestClient(mod.app) as client:
                assert client.post("/api/save", json={"url": URL, "destination": "local"}).status_code == 401
                monkeypatch.setattr(mod, "inspect_post", lambda tweet_id: {"has_video": False})
                assert client.post("/api/save", headers=AUTH,
                                   json={"url": URL, "destination": "local"}).status_code == 400