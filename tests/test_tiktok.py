"""TikTok video URL security, download lifecycle and X compatibility."""
import importlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from src import tiktok

TOKEN = "test-secret-token-01234567890123"
AUTH = {"Authorization": "Bearer " + TOKEN}
TIKTOK = "https://www.tiktok.com/@example/video/1234567890123456789"


async def idle():
    import asyncio
    await asyncio.sleep(300)


def init(monkeypatch, path):
    monkeypatch.setenv("DATA_DIR", str(path))
    monkeypatch.setenv("APP_TOKEN", TOKEN)
    monkeypatch.setenv("NOTION_TOKEN", "")
    monkeypatch.setenv("NOTION_PARENT_PAGE_ID", "")
    import src.app as app
    importlib.reload(app)
    return app


@pytest.mark.parametrize("value,want", [
    (TIKTOK, TIKTOK),
    ("看看视频：" + TIKTOK + "?is_from_webapp=1 好看", TIKTOK),
    ("https://vm.tiktok.com/ZM1xABCD/", "https://vm.tiktok.com/ZM1xABCD/"),
    ("https://vt.tiktok.com/ZSRN6hP2/", "https://vt.tiktok.com/ZSRN6hP2/"),
    ("https://www.tiktok.com/t/ZTxyz123/", "https://www.tiktok.com/t/ZTxyz123/"),
])
def test_tiktok_url_parser(value, want):
    assert tiktok.parse_tiktok_url(value) == want


@pytest.mark.parametrize("value", [
    "https://evil.com/path", "http://www.tiktok.com/@example/video/123",
    "https://www.tiktok.com.evil.org/@example/video/123",
    "https://www.tiktok.com/@example", "https://www.tiktok.com/live",
    "https://www.tiktok.com/@example/photo/123",
    "https://www.tiktok.com@evil.org/@example/video/123",
    "https://www.tiktok.com:444/@example/video/123",
    "https://www.tiktok.com/@example/video/123/../../admin",
])
def test_tiktok_reject_unsupported_url(value):
    with pytest.raises(tiktok.TikTokError):
        tiktok.parse_tiktok_url(value)


def test_tiktok_preview_download_and_cleanup(monkeypatch):
    with TemporaryDirectory() as tmp:
        app = init(monkeypatch, tmp)

        def fake_inspect(url):
            assert url == TIKTOK
            return {"id": "1234567890123456789", "title": "小视频",
                    "author": "example", "duration": 12, "thumbnail": None}

        def fake_download(url, folder, max_file_mb):
            assert url == TIKTOK
            assert max_file_mb > 0
            path = Path(folder) / "tiktok-1234567890123456789.mp4"
            path.write_bytes(b"fake-tiktok-video")
            return path

        with patch.object(app,"queue_worker",side_effect=idle), patch.object(
                app.tiktok_video,"inspect_tiktok",side_effect=fake_inspect), patch.object(
                app.tiktok_video,"download_tiktok",side_effect=fake_download):
            with TestClient(app.app) as c:
                assert c.post("/api/tiktok/prepare",json={"url":TIKTOK}).status_code == 401
                response = c.post("/api/tiktok/prepare",headers=AUTH,json={"url":TIKTOK})
                assert response.status_code == 200
                info = response.json()
                assert info["status"] == "download_ready"
                assert info["preview"]["title"] == "小视频"
                assert TOKEN not in info["download_url"]
                file = c.get(info["download_url"])
                assert file.status_code == 200
                assert file.content == b"fake-tiktok-video"
                assert file.headers["content-type"].startswith("video/mp4")
                assert "tiktok-123" in file.headers["content-disposition"]
                assert c.get(info["download_url"]).status_code == 404
                assert not list(app.TEMP_DIR.glob("transfer-tiktok-*"))
                assert c.get("/api/archives",headers=AUTH).json()["items"] == []


def test_tiktok_shortcut_share_keeps_x_notion_untouched(monkeypatch):
    with TemporaryDirectory() as tmp:
        app = init(monkeypatch,tmp)
        monkeypatch.setattr(app.tiktok_video,"inspect_tiktok",
                            lambda url: {"id":"123","title":"A","author":"","duration":2,"thumbnail":None})
        with patch.object(app,"queue_worker",side_effect=idle):
            with TestClient(app.app) as c:
                dl = c.post("/api/shortcut/save", headers=AUTH,
                            json={"url":"分享 " + TIKTOK, "video_action":"download"}).json()
                assert dl["status"] == "download_ready"
                assert "/api/tiktok/download/" in dl["download_url"]
                old = c.post("/api/shortcut/save", headers=AUTH,
                             json={"url":TIKTOK}).json()
                assert old["status"] == "setup_required"
                assert "Notion" in old["message"]


def test_tiktok_preview_rejects_playlist(monkeypatch):
    class YDL:
        def __init__(self, opts): pass
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def extract_info(self, url, download=False):
            return {"_type":"playlist","id":"abc"}
    monkeypatch.setattr(tiktok.yt_dlp,"YoutubeDL",YDL)
    with pytest.raises(tiktok.TikTokError, match="图集"):
        tiktok.inspect_tiktok(TIKTOK)


def test_tiktok_size_limit_and_temporary_cleanup(monkeypatch):
    class YDL:
        def __init__(self,opts):
            self.opts=opts
            # Regression: yt-dlp raises MaxDownloadsReached even after a
            # successful single video when max_downloads=1 was configured.
            assert "max_downloads" not in opts
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def extract_info(self,url,download=True):
            Path(self.opts["outtmpl"].replace("%(id)s","123").replace("%(ext)s","mp4")).write_bytes(b"a"*1024)
    monkeypatch.setattr(tiktok.yt_dlp,"YoutubeDL",YDL)
    with TemporaryDirectory() as tmp:
        result=tiktok.download_tiktok(TIKTOK, Path(tmp), max_file_mb=1)
        assert result.exists()
        assert result.suffix == ".mp4"