"""V2.1 reading layout, source preservation and in-place schema upgrades."""
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.notion import NotionPublisher
from src.notion_reading import reading_blocks, author_label, thread_members
from src import notion_database
from tests.test_notion_database import init, idle, AUTH, PARENT, DATABASE, SOURCE


def content(block):
    return "".join(item["text"]["content"] for item in
                   block.get(block["type"], {}).get("rich_text", []))


def source_properties():
    return {name: dict(spec, id="prop-" + str(index)) for index, (name, spec) in
            enumerate(notion_database.fields().items())}


def post(id="123", text="正文", author="writer", **extra):
    return dict(id=id, text=text, author_username=author, author_name="作者",
                url="https://x.com/i/status/" + id, created_at="", media=[], **extra)


def published(posts):
    publisher = NotionPublisher("fake", PARENT)
    publisher.create_page = lambda _: ("page", "https://notion.so/page")
    blocks = []
    publisher.append = lambda _, items: blocks.extend(items)
    publisher._article_image_block = lambda url, warnings, caption="": {
        "object": "block", "type": "image", "image": {"type": "external",
        "external": {"url": url}}}
    publisher.publish({"tweet_id": "123", "source": "https://x.com/i/status/123",
                       "posts": posts, "errors": []}, Path("."), metadata_only=True)
    return blocks


def test_schema_order_and_author_without_ai_values():
    assert list(notion_database.fields()) == [
        "标题", "作者", "来源", "内容类型", "阅读状态", "用途", "分类",
        "原文链接", "收藏时间", "AI关键词", "AI摘要"]
    props = notion_database.page_properties("标题", "X", "帖子", "https://x.com/i/status/1", "姓名 (@writer)")
    assert props["作者"]["rich_text"][0]["text"]["content"] == "姓名 (@writer)"
    assert "分类" not in props and "用途" not in props
    assert "AI关键词" not in props and "AI摘要" not in props


def test_article_native_headings_paragraphs_and_original_image_positions():
    text = "题目\n\n## 章节\r\n\r\n第一段\n\n\n![图](https://pbs.twimg.com/media/a.jpg)\n\n第二段"
    blocks = published([post(text=text, is_article=True, article_title="题目")])
    assert blocks[0]["type"] == "heading_1" and content(blocks[0]) == "题目"
    assert sum(content(b) == "题目" for b in blocks) == 1
    assert any(content(b) == "作者：作者 (@writer)" for b in blocks)
    assert any(b["type"] == "table_of_contents" for b in blocks)
    chapter = next(i for i, b in enumerate(blocks) if content(b) == "章节")
    image = next(i for i, b in enumerate(blocks) if b["type"] == "image")
    first = next(i for i, b in enumerate(blocks) if content(b) == "第一段")
    last = next(i for i, b in enumerate(blocks) if content(b) == "第二段")
    assert blocks[chapter]["type"] == "heading_2"
    assert chapter < first < image < last


def test_reading_blocks_preserve_soft_lines_lists_quotes_and_long_text():
    text = "第一行\n第二行\n\n- 条目\n1. 顺序\n> 引用\n\n" + "完整句子。" * 600
    blocks = reading_blocks(text)
    assert [b["type"] for b in blocks[:4]] == [
        "paragraph", "bulleted_list_item", "numbered_list_item", "quote"]
    assert content(blocks[0]) == "第一行\n第二行"
    assert "".join(content(b) for b in blocks[4:]) == "完整句子。" * 600
    assert all(len(content(b)) <= 1800 for b in blocks)


def test_fenced_code_preserves_whitespace_and_does_not_upload_image_markers():
    images = []
    text = '~~~python\n  x = "![sample](https://pbs.twimg.com/a.jpg)"\n\n    y = 1\n~~~\n\n正文'
    blocks = reading_blocks(text, image_block=lambda *args: images.append(args))
    assert not images
    assert blocks[0]["type"] == "code"
    assert content(blocks[0]) == '  x = "![sample](https://pbs.twimg.com/a.jpg)"\n\n    y = 1'
    assert content(blocks[1]) == "正文"


def test_unclosed_code_and_literal_escape_are_not_discarded():
    text = r"literal \n stays" + "\n\n" + chr(96) * 3 + "python\n  unfinished"
    blocks = reading_blocks(text)
    assert content(blocks[0]) == r"literal \n stays"
    assert content(blocks[1]) == chr(96) * 3 + "python\n  unfinished"


def test_inline_image_between_words_preserves_both_sides():
    pub = NotionPublisher("fake", PARENT)
    pub._article_image_block = lambda url, warnings, caption: {"type": "image"}
    blocks = pub._article_body_blocks("前文![图片](https://pbs.twimg.com/a.jpg)后文", [])
    assert [b["type"] for b in blocks] == ["paragraph", "image", "paragraph"]
    assert [content(blocks[0]), content(blocks[2])] == ["前文", "后文"]


def test_thread_navigation_numbering_and_quote_does_not_consume_number():
    posts = [post(text="第一段\n\n第二个段落"),
             post("9", "引用内容", "other", is_quote=True),
             post("124", "下一条")]
    blocks = published(posts)
    heads = [content(b) for b in blocks if b["type"] == "heading_2"]
    assert heads == ["1/2 · 作者 (@writer)", "2/2 · 作者 (@writer)"]
    assert any(b["type"] == "heading_3" and content(b).startswith("引用帖") for b in blocks)
    assert any("已获取 2 段" in content(b) for b in blocks)
    assert sum(b["type"] == "table_of_contents" for b in blocks) == 1
    assert any(content(b) == "第二个段落" for b in blocks)
    links = [b["paragraph"]["rich_text"][0]["text"].get("link", {}).get("url")
             for b in blocks if b["type"] == "paragraph" and content(b) == "在 X 打开"]
    assert links == [p["url"] for p in posts]


@pytest.mark.parametrize("posts", [
    [post(), post("9", is_quote=True)], [post(), post("124", author="other")],
    [post(author=""), post("124", author="")], [post(is_article=True), post("124")],
])
def test_single_quote_unrelated_authors_and_articles_are_not_threads(posts):
    assert not thread_members(posts)
    if not posts[0].get("is_article"):
        assert not any(b["type"] == "table_of_contents" for b in published(posts))


def test_author_fallback_is_readable():
    assert author_label({"author_name": "姓名"}) == "姓名"
    assert author_label({"author_username": "@writer"}) == "@writer"
    assert author_label({}) == "未知"


class SchemaAPI:
    def __init__(self, props=None, view_failure=False):
        self.props = deepcopy(props if props is not None else source_properties())
        self.calls = []
        self.view_failure = view_failure

    def call(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if path == "/data_sources/" + SOURCE:
            if method == "PATCH":
                for key, spec in kwargs["json"]["properties"].items():
                    if "name" in spec:
                        old_name = next(name for name, value in self.props.items() if value.get("id") == key or name == key)
                        self.props[spec["name"]] = self.props.pop(old_name)
                    else:
                        self.props[key] = dict(spec, id="new-" + key)
            return {"properties": deepcopy(self.props),
                    "parent": {"type": "database_id", "database_id": DATABASE}}
        if path == "/views":
            return {"results": [{"object": "view", "id": "view"}], "has_more": False}
        if path == "/views/view":
            if method == "PATCH":
                if self.view_failure:
                    raise RuntimeError("restricted")
                return {}
            return {"type": "table", "data_source_id": SOURCE, "name": "待阅读",
                    "configuration": {"type": "table", "properties": [],
                                      "wrap_cells": True, "frozen_column_index": 1},
                    "filter": {"property": "阅读状态", "select": {"equals": "待读"}}}
        if path == "/pages":
            return {"id": "page", "url": "https://notion.so/page"}
        raise AssertionError((method, path))


def legacy_properties():
    props = source_properties()
    props.pop("作者")
    props["关键词"] = props.pop("AI关键词")
    props["关键词"]["multi_select"]["options"] = [{"id": "tag", "name": "保留标签"}]
    props["AI 摘要"] = props.pop("AI摘要")
    props["个人评分"] = {"id": "custom", "type": "number", "number": {}}
    return props


def test_existing_schema_migration_keeps_ids_options_and_custom_fields():
    api = SchemaAPI(legacy_properties())
    old = deepcopy(api.props)
    props, changed, warnings = notion_database.ensure_schema(api, SOURCE)
    assert changed and not warnings
    assert props["AI关键词"] == old["关键词"]
    assert props["AI摘要"] == old["AI 摘要"]
    assert props["个人评分"] == old["个人评分"]
    payload = next(kwargs["json"]["properties"] for method, path, kwargs in api.calls if method == "PATCH" and path.startswith("/data_sources/"))
    assert old["关键词"]["id"] in payload and old["AI 摘要"]["id"] in payload
    assert all(value is not None for value in payload.values())
    assert "阅读状态" not in payload and "分类" not in payload and "用途" not in payload
    api.calls.clear()
    assert notion_database.ensure_schema(api, SOURCE)[1] is False
    assert not any(method == "PATCH" for method, _, _ in api.calls)


def test_existing_view_order_preserves_custom_columns_visibility_and_widths():
    props = source_properties()
    props["自定义"] = {"id": "custom", "type": "number"}
    existing = [{"property_id": props["AI摘要"]["id"], "visible": False, "width": 200},
                {"property_id": props["标题"]["id"], "visible": True, "width": 333},
                {"property_id": "custom", "visible": False, "wrap": True}]
    columns = notion_database.ordered_columns(props, existing)
    assert columns[0]["width"] == 333
    assert columns[-1] == existing[0]
    assert columns[-3] == existing[2]
    assert columns[-2]["property_id"] == props["AI关键词"]["id"]


def test_migration_view_failure_is_reported_without_losing_author_column():
    api = SchemaAPI(legacy_properties(), view_failure=True)
    props, changed, warnings = notion_database.ensure_schema(api, SOURCE)
    assert changed and "作者" in props and warnings
    assert "列顺序未更新" in warnings[0]


def test_schema_type_conflict_rejects_before_any_mutation():
    props = legacy_properties()
    props["关键词"]["type"] = "number"
    api = SchemaAPI(props)
    with pytest.raises(RuntimeError, match="类型不兼容"):
        notion_database.ensure_schema(api, SOURCE)
    assert not any(method == "PATCH" for method, _, _ in api.calls)


def test_schema_upgrade_failure_does_not_create_an_orphan_page():
    publisher = NotionPublisher("fake", PARENT, data_source_id=SOURCE)
    calls = []
    def fail(method, path, **kwargs):
        calls.append(path)
        raise RuntimeError("missing update permission")
    publisher.call = fail
    with pytest.raises(RuntimeError):
        publisher.create_collection_page("title", "https://x.com/i/status/1", "X", "帖子")
    assert "/pages" not in calls


def test_tiktok_author_is_written_and_source_link_retained():
    pub = NotionPublisher("fake", PARENT, data_source_id=SOURCE)
    api = SchemaAPI()
    pub.call = api.call
    blocks = []
    pub.append = lambda _, items: blocks.extend(items)
    pub.publish_tiktok("https://www.tiktok.com/@writer/video/1", {"title": "视频", "author": "TikTok 作者"})
    payload = next(kwargs["json"] for _, path, kwargs in api.calls if path == "/pages")
    assert payload["properties"]["作者"]["rich_text"][0]["text"]["content"] == "TikTok 作者"
    assert payload["properties"]["原文链接"]["url"].startswith("https://www.tiktok.com/")
    assert any("原视频" in content(b) for b in blocks)


def test_view_pagination_and_other_sources_are_preserved():
    api = SchemaAPI()
    base = api.call
    def call(method, path, **kwargs):
        if path == "/views":
            api.calls.append((method, path, kwargs))
            if kwargs["params"].get("start_cursor") == "next":
                return {"results": [{"id": "view"}], "has_more": False}
            return {"results": [{"id": "other"}], "has_more": True, "next_cursor": "next"}
        if path == "/views/other":
            return {"type": "table", "data_source_id": "other-source"}
        return base(method, path, **kwargs)
    api.call = call
    assert not notion_database.reorder_views(api, DATABASE, SOURCE, api.props)
    assert not any(method == "PATCH" and path == "/views/other" for method, path, _ in api.calls)
    assert sum(path == "/views" for _, path, _ in api.calls) == 2
    payload = next(kwargs["json"] for method, path, kwargs in api.calls if method == "PATCH")
    assert payload["configuration"]["wrap_cells"] is True
    assert payload["configuration"]["frozen_column_index"] == 1
    assert "filter" not in payload and "sorts" not in payload


def test_upgrade_route_auth_binding_and_success(monkeypatch, tmp_path):
    app = init(monkeypatch, tmp_path)
    api = SchemaAPI()
    with patch.object(app, "queue_worker", side_effect=idle):
        with TestClient(app.app) as client:
            assert client.post("/api/notion/database/upgrade").status_code == 401
            assert client.post("/api/notion/database/upgrade", headers=AUTH).status_code == 409
            with app.conn() as db:
                app.notion_config.save_settings(db, app.APP_TOKEN, "notion-user-token", PARENT, "收藏")
                db.execute("INSERT INTO notion_collection VALUES(1,?,?,?,?,?)",
                           (PARENT, DATABASE, SOURCE, "https://notion.so/database", 1))
            with patch.object(app, "NotionPublisher", return_value=api):
                response = client.post("/api/notion/database/upgrade", headers=AUTH)
            assert response.status_code == 200 and response.json()["warnings"] == []
            assert "V2.1" in response.json()["message"]
            assert not any(path == "/databases" for _, path, _ in api.calls)
            with patch.object(app, "NotionPublisher", side_effect=RuntimeError("private-token")):
                failed = client.post("/api/notion/database/upgrade", headers=AUTH)
            assert failed.status_code == 502 and "private-token" not in failed.text

def test_thread_order_handles_numeric_ids_missing_and_invalid_timestamps():
    from src.archive import normalize_thread
    records = [{"id": "10", "created_timestamp": "bad"},
               {"id": "9"}, {"id": "8", "created_timestamp": float("nan")}]
    ordered, _ = normalize_thread({"thread": records, "status": records[0]})
    assert [p["id"] for p in ordered] == ["8", "9", "10"]
    snowflakes = [{"id": "2108132811635032316"}, {"id": "2108132811635032317"}]
    ordered, _ = normalize_thread({"thread": list(reversed(snowflakes))})
    assert [p["id"] for p in ordered] == [p["id"] for p in snowflakes]


def test_article_image_limit_preserves_remaining_links_and_tail(monkeypatch):
    from src import notion
    monkeypatch.setattr(notion, "MAX_ARTICLE_IMAGES", 1)
    pub = NotionPublisher("fake", PARENT)
    images = []
    def image(url, warnings, caption):
        images.append(url)
        return {"type": "image"}
    pub._article_image_block = image
    warnings = []
    blocks = pub._article_body_blocks(
        "第一段\n\n![一](https://pbs.twimg.com/one.jpg)\n\n第二段\n\n"
        "![二](https://pbs.twimg.com/two.jpg)\n\n最后一段", warnings)
    assert images == ["https://pbs.twimg.com/one.jpg"]
    link = next(b for b in blocks if content(b).startswith("文章配图"))
    assert link["paragraph"]["rich_text"][0]["text"]["link"]["url"] == "https://pbs.twimg.com/two.jpg"
    assert content(blocks[-1]) == "最后一段"
    assert len(warnings) == 1


def test_new_database_table_views_use_real_ordered_property_ids():
    api = SchemaAPI()
    original = api.call
    def call(method, path, **kwargs):
        if path == "/databases" and method == "POST":
            api.calls.append((method, path, kwargs))
            return {"id": DATABASE, "data_sources": [{"id": SOURCE}]}
        if path == "/views" and method == "POST":
            api.calls.append((method, path, kwargs))
            return {"id": "created-view"}
        return original(method, path, **kwargs)
    api.call = call
    result = notion_database.create("fake", PARENT, publisher_factory=lambda *_: api)
    assert not result["view_warnings"]
    created_views = [kwargs["json"] for method, path, kwargs in api.calls
                     if method == "POST" and path == "/views"]
    assert len(created_views) == 3
    expected = [api.props[name]["id"] for name in notion_database.fields()]
    assert all([p["property_id"] for p in view["configuration"]["properties"]] == expected
               for view in created_views)
    assert any(method == "PATCH" and path == "/views/view" for method, path, _ in api.calls)
