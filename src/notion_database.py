"""X Vault Notion database bootstrap, metadata schema and durable selection.

Notion API 2026-03-11 separates database containers from data sources.
Collection initialization is an explicit authenticated user action; importing
this module never creates resources in the user's workspace.
"""
import re
import threading
import time

from .notion import NotionPublisher

_LOCK = threading.Lock()
UUID = re.compile(r"^[0-9a-fA-F-]{32,36}$")
COLLECTION_NAME = "X Vault 收藏库"


def fields():
    def select(names):
        return {"type": "select", "select": {"options": [{"name": name} for name in names]}}
    return {
        "标题": {"type": "title", "title": {}},
        "来源": select(["X", "TikTok"]),
        "内容类型": select(["文章", "帖子", "视频"]),
        "分类": select(["AI工具", "技术开发", "产品创业", "商业", "生活", "其他"]),
        "用途": select(["待实践", "知识参考", "观点", "其他"]),
        # Explicit manual user choice. AI must never update this field.
        "阅读状态": select(["待读", "已读", "已实践"]),
        "关键词": {"type": "multi_select", "multi_select": {"options": []}},
        "AI 摘要": {"type": "rich_text", "rich_text": {}},
        "原文链接": {"type": "url", "url": {}},
        "收藏时间": {"type": "created_time", "created_time": {}},
    }


def init_table(db):
    db.execute("""CREATE TABLE IF NOT EXISTS notion_collection (
        id INTEGER PRIMARY KEY CHECK (id=1),
        parent_page_id TEXT NOT NULL,
        database_id TEXT NOT NULL,
        data_source_id TEXT NOT NULL,
        database_url TEXT NOT NULL,
        created_at INTEGER NOT NULL)""")


def read(db):
    return db.execute("SELECT * FROM notion_collection WHERE id=1").fetchone()


def clear(db):
    db.execute("DELETE FROM notion_collection WHERE id=1")


def create(token, parent_page_id, *, publisher_factory=NotionPublisher):
    """Create one database and its initial data source under a verified page."""
    notion = publisher_factory(token, parent_page_id)
    created = notion.call("POST", "/databases", json={
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": [{"type": "text", "text": {"content": COLLECTION_NAME}}],
        "initial_data_source": {"properties": fields()},
        "is_inline": False,
    })
    dbid = str(created.get("id") or "")
    sources = created.get("data_sources") or []
    source_id = str(sources[0].get("id") or "") if sources and isinstance(sources[0], dict) else ""
    # Some responses don't include data_sources: retrieve before writing config.
    if not source_id and dbid:
        info = notion.call("GET", f"/databases/{dbid}")
        sources = info.get("data_sources") or []
        source_id = str(sources[0].get("id") or "") if sources and isinstance(sources[0], dict) else ""
    if not UUID.fullmatch(dbid) or not UUID.fullmatch(source_id):
        raise RuntimeError("Notion 已尝试创建数据库，但未返回可用的数据源 ID；请检查 Notion 页面，避免重复创建。")
    # Views are optional conveniences. The database remains usable even if the
    # Notion account denies Update content or view creation is unavailable.
    view_warnings = []
    for name, property_name, value in (
        ("待阅读", "阅读状态", "待读"),
        ("待实践", "用途", "待实践"),
        ("已读归档", "阅读状态", "已读"),
    ):
        try:
            notion.call("POST", "/views", json={
                "database_id": dbid, "data_source_id": source_id,
                "name": name, "type": "table",
                "filter": {"property": property_name, "select": {"equals": value}},
            })
        except Exception:
            view_warnings.append(name)
    return {"database_id": dbid, "data_source_id": source_id,
            "view_warnings": view_warnings,
            "database_url": str(created.get("url") or "https://www.notion.so/" + dbid.replace("-", ""))}


def ensure_initialized(db_connection, token, parent_page_id, *, factory=None):
    """Serialize requests; don't create again for an already configured page."""
    with _LOCK:
        with db_connection() as db:
            existing = read(db)
            if existing:
                if existing["parent_page_id"].replace("-", "") == parent_page_id.replace("-", ""):
                    return dict(existing), False
                raise ValueError("收藏父页面已改变，请检查连接配置，不能继续使用旧数据库")
        new = create(token, parent_page_id, publisher_factory=factory or NotionPublisher)
        with db_connection() as db:
            db.execute("""INSERT INTO notion_collection
                (id,parent_page_id,database_id,data_source_id,database_url,created_at)
                VALUES (1,?,?,?,?,?)""", (parent_page_id, new["database_id"],
                    new["data_source_id"], new["database_url"], int(time.time())))
            return dict(read(db)), True


def page_properties(title, source, content_type, original_url):
    """Keep user-owned classification and reading state untouched by AI."""
    return {
        "标题": {"title": [{"type": "text", "text": {"content": str(title)[:95]}}]},
        "来源": {"select": {"name": source}},
        "内容类型": {"select": {"name": content_type}},
        "阅读状态": {"select": {"name": "待读"}},
        "原文链接": {"url": original_url},
    }


def reconnect(db_connection, token, parent_page_id, database_id, *, publisher_factory=NotionPublisher):
    """Rebind an existing database after ephemeral service settings are reset."""
    from .notion_config import parse_page_id, NotionSetupError
    dbid = parse_page_id(database_id)
    notion = publisher_factory(token, parent_page_id)
    obj = notion.call("GET", f"/databases/{dbid}")
    if obj.get("object") != "database":
        raise NotionSetupError("链接不是一个可访问的 Notion 数据库")
    parent = obj.get("parent") or {}
    parent_id = str(parent.get("page_id") or "").replace("-", "").lower()
    if parent.get("type") != "page_id" or parent_id != parent_page_id.replace("-", "").lower():
        raise NotionSetupError("该数据库不在已授权的收藏父页面下，请选择正确的数据库链接")
    sources = obj.get("data_sources") or []
    source_id = str(sources[0].get("id") or "") if sources and isinstance(sources[0], dict) else ""
    if not UUID.fullmatch(source_id):
        raise NotionSetupError("该数据库没有可用的数据源")
    source = notion.call("GET", f"/data_sources/{source_id}")
    props = source.get("properties") or {}
    needed = {"标题": "title", "原文链接": "url", "阅读状态": "select",
              "来源": "select", "内容类型": "select"}
    if not all(isinstance(props.get(k), dict) and props[k].get("type") == v for k, v in needed.items()):
        raise NotionSetupError("该数据库缺少 X Vault 所需字段，不能作为收藏数据库绑定")
    with _LOCK:
        with db_connection() as db:
            row = read(db)
            if row and row["database_id"].replace("-", "") != dbid.replace("-", ""):
                raise NotionSetupError("已有其他数据库绑定；请先解除连接后重新配置")
            if not row:
                db.execute("""INSERT INTO notion_collection
                    (id,parent_page_id,database_id,data_source_id,database_url,created_at)
                    VALUES (1,?,?,?,?,?)""", (parent_page_id, dbid, source_id,
                           obj.get("url") or "https://www.notion.so/" + dbid, int(time.time())))
            return dict(read(db))