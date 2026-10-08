"""Single-user web Notion setup regression tests."""
import importlib
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from src import notion_config

TOKEN = "test-secret-token-01234567890123"
AUTH = {"Authorization": "Bearer "+TOKEN}
PAGE_ID = "1234567890abcdef1234567890abcdef"
NOTION_URL = "https://www.notion.so/X-Vault-1234567890abcdef1234567890abcdef"

async def idle():
    import asyncio
    await asyncio.sleep(300)

def initialize(monkeypatch, tmp, env_notions=False):
    monkeypatch.setenv("DATA_DIR", tmp)
    monkeypatch.setenv("APP_TOKEN", TOKEN)
    monkeypatch.setenv("NOTION_TOKEN", "legacy-key" if env_notions else "")
    monkeypatch.setenv("NOTION_PARENT_PAGE_ID", PAGE_ID if env_notions else "")
    import src.app as app
    importlib.reload(app)
    return app


def test_page_url_parser_safety():
    assert notion_config.parse_page_id(NOTION_URL) == PAGE_ID
    assert notion_config.parse_page_id(PAGE_ID) == PAGE_ID
    assert notion_config.parse_page_id("https://myspace.notion.site/Some-"+PAGE_ID) == PAGE_ID
    for invalid in ["https://evil.com/"+PAGE_ID, "http://www.notion.so/"+PAGE_ID,
                    "https://notion.so.evil.com/"+PAGE_ID, "oops"]:
        with pytest.raises(notion_config.NotionSetupError):
            notion_config.parse_page_id(invalid)


def test_connection_setup_test_and_disconnect(monkeypatch):
    with TemporaryDirectory() as tmp:
        app=initialize(monkeypatch,tmp)
        with patch.object(app,"queue_worker",side_effect=idle), patch.object(
             app.notion_config, "validate_page", return_value="个人 X 收藏") as check:
            with TestClient(app.app) as c:
                assert c.get("/api/notion/connection").status_code == 401
                assert c.post("/api/notion/configure", json={
                    "token":"secret", "page_url":NOTION_URL}).status_code == 401
                assert c.get("/api/notion/connection",headers=AUTH).json()["connected"] is False
                configured=c.post("/api/notion/configure",headers=AUTH,json={
                    "token":"notion-private-secret","page_url":NOTION_URL})
                assert configured.status_code == 200
                assert configured.json()["connected"] is True
                check.assert_called_once_with("notion-private-secret",PAGE_ID)
                with app.conn() as db:
                    raw=app.notion_config.read_settings(db)
                    assert raw["page_id"]==PAGE_ID
                    assert "notion-private-secret" not in raw["token_encrypted"]
                info=c.get("/api/notion/connection",headers=AUTH).json()
                assert info["page_title"]=="个人 X 收藏"
                assert "secret" not in str(info)
                assert app.notion_credentials()==("notion-private-secret",PAGE_ID,"saved")
                result=c.post("/api/notion/disconnect",headers=AUTH)
                assert result.json()["connected"] is False
                assert app.notion_credentials()==(None,None,"none")


def test_failed_validation_does_not_overwrite(monkeypatch):
    with TemporaryDirectory() as tmp:
        app=initialize(monkeypatch,tmp)
        with patch.object(app,"queue_worker",side_effect=idle):
            with TestClient(app.app) as c:
                with patch.object(app.notion_config,"validate_page",return_value="原收藏页"):
                    ok=c.post("/api/notion/configure",headers=AUTH,json={
                        "token":"good-secret","page_url":NOTION_URL})
                    assert ok.status_code==200
                with patch.object(app.notion_config,"validate_page",side_effect=notion_config.NotionSetupError("未添加连接")):
                    invalid=c.post("/api/notion/configure",headers=AUTH,json={
                        "token":"wrong-secret","page_url":NOTION_URL})
                assert invalid.status_code==400
                assert app.notion_credentials()==("good-secret",PAGE_ID,"saved")


def test_legacy_env_fallback(monkeypatch):
    with TemporaryDirectory() as tmp:
        app=initialize(monkeypatch,tmp,True)
        with patch.object(app,"queue_worker",side_effect=idle):
            with TestClient(app.app) as c:
                info=c.get("/api/notion/connection",headers=AUTH).json()
                assert info["connected"] is True and info["mode"] == "environment"
                assert app.notion_credentials()==("legacy-key",PAGE_ID,"manual")


def test_notions_errors():
    class Fake:
        def __init__(self,code,doc=None):
            self.status_code=code
            self.ok=code==200
            self._doc=doc or {"object":"page","properties":{"title":{"type":"title","title":[{"plain_text":"X 收藏"}]}}}
        def json(self): return self._doc
    class Session:
        def __init__(self,code): self.code=code
        def get(self,*args,**kwargs): return Fake(self.code)
    assert notion_config.validate_page("secret",PAGE_ID,session=Session(200))=="X 收藏"
    for code in (401,403,404,429,502):
        with pytest.raises(notion_config.NotionSetupError):
            notion_config.validate_page("secret",PAGE_ID,session=Session(code))


def test_encrypted_token_wrong_key():
    encrypted=notion_config.encrypt_token(TOKEN,"notion-value")
    assert notion_config.decrypt_token(TOKEN,encrypted)=="notion-value"
    with pytest.raises(notion_config.NotionSetupError):
        notion_config.decrypt_token("different-deployment-token-123456789",encrypted)
def test_notion_400_returns_specific_safe_diagnostics():
    """Surface upstream Notion code/message rather than opaque HTTP 400."""
    class Response:
        status_code = 400
        ok = False
        def json(self):
            return {"code": "validation_error",
                    "message": "Expected a page id; secret_SUPERSECRET42 is not valid."}
    class Session:
        def get(self, url, **kwargs):
            self.url = url
            assert kwargs["headers"]["Notion-Version"] == "2026-03-11"
            assert kwargs["headers"]["Authorization"] == "Bearer secret_SUPERSECRET42"
            return Response()

    session = Session()
    with pytest.raises(notion_config.NotionSetupError) as captured:
        notion_config.validate_page("secret_SUPERSECRET42", PAGE_ID, session=session)
    text = str(captured.value)
    assert "HTTP 400" in text and "validation_error" in text
    assert "请确认链接来自普通 Notion 页面" in text
    assert "secret_SUPERSECRET42" not in text
    assert "/pages/12345678-90ab-cdef-1234-567890abcdef" in session.url


def test_unknown_notion_400_has_actionable_message():
    class Response:
        status_code = 400
        ok = False
        def json(self):
            return {"code": "invalid_request_url", "message": "Invalid URL."}
    class Session:
        def get(self, *args, **kwargs): return Response()
    with pytest.raises(notion_config.NotionSetupError) as captured:
        notion_config.validate_page("test-notion-internal-token", PAGE_ID, session=Session())
    assert "invalid_request_url" in str(captured.value)
    assert "复制链接" in str(captured.value)


def test_bad_notion_id_rejected_before_request():
    class Session:
        def get(self, *args, **kwargs):
            raise AssertionError("Do not contact Notion for an invalid page ID")
    with pytest.raises(notion_config.NotionSetupError, match="页面 ID"):
        notion_config.validate_page("secret", "not-a-page", session=Session())


def test_web_response_shows_notion_upstream_400_details(monkeypatch):
    """Verify the exact error is returned to the owner, without credentials."""
    with TemporaryDirectory() as tmp:
        app = initialize(monkeypatch, tmp)
        with patch.object(app, "queue_worker", side_effect=idle):
            with TestClient(app.app) as c:
                with patch.object(app.notion_config, "validate_page",
                    side_effect=notion_config.NotionSetupError("Notion 返回 HTTP 400（validation_error）：链接不是普通页面")):
                    bad = c.post("/api/notion/configure", headers=AUTH,
                        json={"token": "notion-private-secret", "page_url": NOTION_URL})
                assert bad.status_code == 400
                assert "validation_error" in bad.json()["detail"]
                assert "notion-private-secret" not in str(bad.json())
                assert c.get("/api/notion/connection", headers=AUTH).json()["connected"] is False


def test_web_config_is_used_by_existing_shortcut(monkeypatch):
    """Web setup and existing iOS endpoint use the exact same stored credentials."""
    with TemporaryDirectory() as tmp:
        app = initialize(monkeypatch, tmp)
        monkeypatch.setattr(app, "SHORTCUT_WAIT_SECONDS", 0)
        monkeypatch.setattr(app, "inspect_post", lambda tweet_id: {"has_video": True})
        fake_archive = {
            "tweet_id": "738291", "source": "https://x.com/poster/status/738291",
            "errors": [], "posts": [{"text": "Post", "author_username": "poster",
                                      "created_at": "", "url": "https://x.com/poster/status/738291",
                                      "media": [{"kind": "video", "file": None}]}],
        }
        def fake_archive_post(tweet_id, path, **kwargs):
            assert kwargs["metadata_only"] is True
            return fake_archive
        monkeypatch.setattr(app, "archive_post", fake_archive_post)

        class FakePublisher:
            def __init__(self, token, parent):
                assert token == "notion-private-secret"
                assert parent == PAGE_ID
            def publish(self, archive, archive_dir, public_base_url, share_key, *,
                        on_created=None, ephemeral=False, metadata_only=False):
                assert metadata_only is True
                on_created("https://notion.so/created-page")
                return "https://notion.so/created-page", []

        monkeypatch.setattr(app, "NotionPublisher", FakePublisher)
        with patch.object(app, "queue_worker", side_effect=idle), patch.object(
             app.notion_config, "validate_page", return_value="X 收藏"):
            with TestClient(app.app) as client:
                configured = client.post("/api/notion/configure", headers=AUTH,
                    json={"token": "notion-private-secret", "page_url": NOTION_URL})
                assert configured.status_code == 200
                payload = {"url": "https://x.com/poster/status/738291"}
                first = client.post("/api/shortcut/save", headers=AUTH, json=payload)
                assert first.json()["status"] == "processing"
                app.process_one("738291")
                second = client.post("/api/shortcut/save", headers=AUTH, json=payload)
                assert second.json()["status"] == "complete"
                assert "Notion" in second.json()["message"]
                assert second.json()["notion_url"] == "https://notion.so/created-page"
