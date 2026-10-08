"""iOS shortcut video download routing and compatibility tests."""
import importlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import urlparse

from fastapi.testclient import TestClient

from test_video_flow import fake_video_archive, idle

TOKEN = "test-secret-token-01234567890123"
AUTH = {"Authorization": "Bearer " + TOKEN}
POST = "https://x.com/poster/status/738291"


def setup(monkeypatch, data_dir, *, notion=False, has_video=True):
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("APP_TOKEN", TOKEN)
    monkeypatch.setenv("NOTION_TOKEN", "fake-notion" if notion else "")
    monkeypatch.setenv("NOTION_PARENT_PAGE_ID", "pageid" if notion else "")
    import src.app as app
    importlib.reload(app)
    monkeypatch.setattr(app, "SHORTCUT_WAIT_SECONDS", 0)
    monkeypatch.setattr(app, "inspect_post", lambda post_id: {"has_video": has_video})
    return app


def test_new_shortcut_video_without_notion_is_one_use_download(monkeypatch):
    with TemporaryDirectory() as tmp:
        app = setup(monkeypatch, tmp)
        monkeypatch.setattr(app, "archive_post", fake_video_archive)
        with patch.object(app, "queue_worker", side_effect=idle):
            with TestClient(app.app) as c:
                result = c.post("/api/shortcut/save", headers=AUTH, json={
                    "url": POST, "video_action": "download"})
                assert result.status_code == 200
                info = result.json()
                assert info["status"] == "download_ready"
                assert "收藏完成" not in info["message"]
                assert info["download_url"].startswith("http://testserver/api/download/")
                assert TOKEN not in info["download_url"]
                assert c.get("/api/archives", headers=AUTH).json()["items"] == []
                file = c.get(info["download_url"])
                assert file.status_code == 200
                assert file.content == b"test-video-bytes"
                assert file.headers["content-type"].startswith("video/mp4")
                assert "x-738291.mp4" in file.headers["content-disposition"]
                assert c.get(info["download_url"]).status_code == 404
                assert not (Path(tmp) / "archives").exists()
                assert not list(app.TEMP_DIR.glob("transfer-*"))


def test_video_download_url_uses_proxy_https(monkeypatch):
    with TemporaryDirectory() as tmp:
        app = setup(monkeypatch, tmp)
        with patch.object(app, "queue_worker", side_effect=idle):
            with TestClient(app.app) as c:
                result = c.post("/api/shortcut/save", headers={
                    **AUTH, "x-forwarded-proto": "https"}, json={
                    "url": POST, "video_action": "download"}).json()
                assert result["status"] == "download_ready"
                assert urlparse(result["download_url"]).scheme == "https"


def test_new_shortcut_text_route_still_requires_notion(monkeypatch):
    with TemporaryDirectory() as tmp:
        app = setup(monkeypatch, tmp, has_video=False)
        with patch.object(app, "queue_worker", side_effect=idle):
            with TestClient(app.app) as c:
                result = c.post("/api/shortcut/save", headers=AUTH, json={
                    "url": POST, "video_action": "download"}).json()
                assert result["status"] == "setup_required"
                assert "download_url" not in result


def test_new_shortcut_images_still_use_notion_queue(monkeypatch):
    with TemporaryDirectory() as tmp:
        app = setup(monkeypatch, tmp, notion=True, has_video=False)
        with patch.object(app, "queue_worker", side_effect=idle):
            with TestClient(app.app) as c:
                result = c.post("/api/shortcut/save", headers=AUTH, json={
                    "url": POST, "video_action": "download"}).json()
                assert result["status"] == "processing"
                with app.conn() as db:
                    row = db.execute("SELECT delivery FROM archives").fetchone()
                    assert row["delivery"] == "archive"


def test_existing_shortcut_video_notion_behavior_is_preserved(monkeypatch):
    with TemporaryDirectory() as tmp:
        app = setup(monkeypatch, tmp, notion=True, has_video=True)
        with patch.object(app, "queue_worker", side_effect=idle):
            with TestClient(app.app) as c:
                result = c.post("/api/shortcut/save", headers=AUTH, json={"url": POST}).json()
                assert result["status"] == "processing"
                assert "download_url" not in result
                with app.conn() as db:
                    row = db.execute("SELECT delivery FROM archives").fetchone()
                    assert row["delivery"] == "notion_metadata"


def test_shortcut_explicit_download_requires_auth_and_enum(monkeypatch):
    with TemporaryDirectory() as tmp:
        app = setup(monkeypatch, tmp)
        with patch.object(app, "queue_worker", side_effect=idle):
            with TestClient(app.app) as c:
                assert c.post("/api/shortcut/save", json={
                    "url": POST, "video_action": "download"}).status_code == 401
                assert c.post("/api/shortcut/save", headers=AUTH, json={
                    "url": POST, "video_action": "typo"}).status_code == 422


def test_signed_template_builds_with_valid_video_branch(monkeypatch):
    import sys
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "ios"))
    from build_shortcut_video import build_video_shortcut
    workflow = build_video_shortcut()
    actions = workflow["WFWorkflowActions"]
    names = [a["WFWorkflowActionIdentifier"] for a in actions]
    assert len(actions) == 13
    assert names[1] == "is.workflow.actions.downloadurl"
    assert names[6] == "is.workflow.actions.downloadurl"
    assert names[7] == "is.workflow.actions.documentpicker.save"
    items = actions[1]["WFWorkflowActionParameters"]["WFJSONValues"]["Value"]["WFDictionaryFieldValueItems"]
    keys = [i["WFKey"]["Value"]["string"] for i in items]
    assert keys == ["url", "video_action"]
    assert items[1]["WFValue"]["Value"]["string"] == "download"
    assert actions[1]["WFWorkflowActionParameters"]["WFURL"].startswith("https://")
    control = [actions[i]["WFWorkflowActionParameters"]["WFControlFlowMode"] for i in (4, 9, 12)]
    assert control == [0, 1, 2]
    assert actions[4]["WFWorkflowActionParameters"]["WFConditionalActionString"] == "download_ready"
    assert actions[7]["WFWorkflowActionParameters"]["WFAskWhereToSave"] is True
    assert actions[7]["WFWorkflowActionParameters"]["WFInput"]["Value"]["OutputUUID"] == actions[6]["WFWorkflowActionParameters"]["UUID"]
    assert {(x["ActionIndex"], x["ParameterKey"]) for x in workflow["WFWorkflowImportQuestions"]} == {
        (0, "WFTextActionText"), (1, "WFURL")}
    assert "video" in workflow["WFWorkflowName"].lower() or "视频" in workflow["WFWorkflowName"]