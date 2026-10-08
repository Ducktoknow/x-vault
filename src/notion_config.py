"""Single-owner Notion settings saved from the X Vault web page.

The user creates an internal Notion connection and grants it access to a
collection page. Only the server receives the token; the browser never reads
it back. One owner per deployment, encrypted SQLite storage.
"""
import base64
import hashlib
import re
import time
from urllib.parse import urlparse

import requests
from cryptography.fernet import Fernet, InvalidToken

NOTION_VERSION = "2026-03-11"
NOTION_HOSTS = {"notion.so", "notion.com", "notion.site"}
ID_PATTERN = re.compile(r"[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")
PAGE_ID_PATTERN = re.compile(r"^(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12})$")


class NotionSetupError(Exception):
    pass


def parse_page_id(value):
    """Accept a Notion page URL or 32-hex UUID, never dereference the URL."""
    value = value.strip()
    if PAGE_ID_PATTERN.fullmatch(value):
        return value.replace("-", "").lower()
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise NotionSetupError("请粘贴 Notion 页面的 HTTPS 链接")
    host = parsed.hostname.lower()
    if not any(host == suffix or host.endswith("." + suffix) for suffix in NOTION_HOSTS):
        raise NotionSetupError("只能填写 Notion 页面链接")
    candidates = ID_PATTERN.findall(parsed.path)
    if not candidates:
        raise NotionSetupError("无法从链接识别页面 ID，请在 Notion 页面点击「复制链接」")
    return candidates[-1].replace("-", "").lower()


def _encryptor(secret):
    if not secret or len(secret) < 24:
        raise NotionSetupError("APP_TOKEN 太短，无法安全保存 Notion 连接")
    key = hashlib.sha256(("x-vault:notion-settings:v1:" + secret).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_token(secret, token):
    return _encryptor(secret).encrypt(token.encode("utf-8")).decode("ascii")


def decrypt_token(secret, blob):
    try:
        return _encryptor(secret).decrypt(blob.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise NotionSetupError("Notion 密钥无法解密；如果修改过 APP_TOKEN，请重新填写 Notion 配置") from exc


def init_tables(db):
    db.execute("""CREATE TABLE IF NOT EXISTS notion_settings (
        id INTEGER PRIMARY KEY CHECK(id=1),
        token_encrypted TEXT NOT NULL,
        page_id TEXT NOT NULL,
        page_title TEXT NOT NULL,
        updated_at INTEGER NOT NULL)""")


def read_settings(db):
    return db.execute("SELECT * FROM notion_settings WHERE id=1").fetchone()


def save_settings(db, secret, token, page_id, page_title):
    db.execute("""INSERT INTO notion_settings (id,token_encrypted,page_id,page_title,updated_at)
                  VALUES(1,?,?,?,?)
                  ON CONFLICT(id) DO UPDATE SET
                    token_encrypted=excluded.token_encrypted,
                    page_id=excluded.page_id, page_title=excluded.page_title,
                    updated_at=excluded.updated_at""",
               (encrypt_token(secret, token), page_id, page_title[:180], int(time.time())))


def validate_page(token, page_id, *, session=None):
    """Validate token and page access via Notion GET; do not write a test page."""
    if not PAGE_ID_PATTERN.fullmatch(page_id):
        raise NotionSetupError("无法识别页面 ID，请从 Notion 普通页面复制完整链接")
    compact_id = page_id.replace("-", "").lower()
    # Notion's retrieve-page endpoint expects a UUID. Use the canonical
    # hyphenated form, not an arbitrary substring from a shared page URL.
    normalized_id = f"{compact_id[:8]}-{compact_id[8:12]}-{compact_id[12:16]}-{compact_id[16:20]}-{compact_id[20:]}"
    client = session or requests
    try:
        response = client.get(
            "https://api.notion.com/v1/pages/" + normalized_id,
            headers={"Authorization": "Bearer " + token, "Notion-Version": NOTION_VERSION},
            timeout=18)
    except requests.RequestException as exc:
        raise NotionSetupError("无法连接 Notion，请检查网络后重试") from exc

    if response.status_code == 401:
        raise NotionSetupError("Notion 密钥无效，请重新复制内部连接密钥")
    if response.status_code in (403, 404):
        raise NotionSetupError("该连接无法访问收藏页面：请在 Notion 页面右上角「··· → 添加连接」授权")
    if response.status_code == 429:
        raise NotionSetupError("Notion 请求过于频繁，请稍后再试")
    if response.status_code == 400:
        # Previously every 400 was reported as an opaque 'HTTP 400'. Notion's
        # JSON body contains code/message needed to identify the real cause.
        try:
            data = response.json()
        except (ValueError, TypeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        code = str(data.get("code") or "unknown")
        code = re.sub(r"[^a-z0-9_]", "", code.lower())[:60] or "unknown"
        message = str(data.get("message") or "")
        message = message.replace(token, "[已隐藏]")
        message = re.sub(r"(?i)\\b(?:ntn_|secret_)[a-z0-9_-]+", "[已隐藏]", message)
        message = " ".join(message.split())[:180]
        hints = {
            "validation_error": "请确认链接来自普通 Notion 页面，不是数据库或工作区首页",
            "invalid_request_url": "请重新从 Notion 普通页面复制链接，确认页面 ID 正确",
            "missing_version": "Notion API 版本设置异常，请检查服务版本",
            "invalid_request": "请检查连接类型以及所选页面是否受 Notion API 支持",
        }
        hint = hints.get(code, "请检查 Notion 连接权限、页面类型和页面链接")
        details = "；Notion 说明：" + message if message else ""
        raise NotionSetupError(f"Notion 返回 HTTP 400（{code}）：{hint}{details}")
    if not response.ok:
        raise NotionSetupError("Notion 连接检查失败（HTTP " + str(response.status_code) + "），请稍后重试")

    try:
        result = response.json()
    except ValueError as exc:
        raise NotionSetupError("Notion 返回数据异常，请稍后重试") from exc
    if result.get("object") != "page" or result.get("archived") or result.get("in_trash"):
        raise NotionSetupError("不是可用的 Notion 普通页面，请重新选择")
    if result.get("is_locked"):
        raise NotionSetupError("Notion 页面已锁定，请先解除锁定")
    if (result.get("parent") or {}).get("type") == "database_id":
        raise NotionSetupError("请使用 Notion 普通页面作为收藏位置，不要使用数据库条目")

    title = "Notion 收藏页面"
    for prop in (result.get("properties") or {}).values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            title = "".join(x.get("plain_text", "") for x in prop.get("title", [])) or title
            break
    return title