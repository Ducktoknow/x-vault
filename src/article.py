"""Convert FxTwitter X Articles (Draft.js) into readable Markdown.

X stores long-form Article bodies separately from ordinary status.text.
Keep block order and do not mistake a short preview for a full article.
"""
import re
from urllib.parse import urlparse

_IMAGE_HOSTS = ("twimg.com", "fxtwitter.com")
_MAX_CHARS = 250_000


def _trusted_image(value):
    if not isinstance(value, str) or not value.startswith("https://"):
        return ""
    try:
        host = (urlparse(value).hostname or "").lower()
    except ValueError:
        return ""
    return value if any(host == name or host.endswith("." + name) for name in _IMAGE_HOSTS) else ""


def _entity_map(data):
    if isinstance(data, dict):
        return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    if isinstance(data, list):
        return {str(x["key"]): x.get("value", x) for x in data
                if isinstance(x, dict) and "key" in x and isinstance(x.get("value", x), dict)}
    return {}


def _media_lookup(article):
    entries = article.get("media_entities") or []
    if isinstance(entries, dict):
        entries = list(entries.values())
    found = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        info = entry.get("media_info") or entry
        if not isinstance(info, dict):
            continue
        src = _trusted_image(info.get("original_img_url") or info.get("media_url_https") or info.get("url"))
        if src:
            for key in (entry.get("media_id"), entry.get("mediaId"), entry.get("id"), entry.get("media_key")):
                if key is not None:
                    found[str(key)] = src
    return found


def _atomic(block, entities, media):
    meta = block.get("data") or {}
    refs = block.get("entityRanges") or []
    if not isinstance(refs, list):
        refs = []
    if not refs and isinstance(meta, dict) and meta.get("entityKey") is not None:
        refs = [{"key": meta["entityKey"]}]
    result = []
    for ref in refs:
        entry = entities.get(str(ref.get("key"))) if isinstance(ref, dict) else None
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("type") or "").upper()
        data = entry.get("data") or {}
        if not isinstance(data, dict):
            continue
        if kind == "MARKDOWN" and isinstance(data.get("markdown"), str):
            result.append(data["markdown"])
        elif kind == "MEDIA":
            for item in data.get("mediaItems") or []:
                if isinstance(item, dict):
                    url = media.get(str(item.get("mediaId")))
                    if url:
                        result.append(f"![文章配图]({url})")
        elif kind == "TWEET" and str(data.get("tweetId") or "").isdigit():
            result.append(f"引用帖子：https://x.com/i/status/{data['tweetId']}")
    return result


def article_markdown(article):
    """Return (title, body, full_body_available), without making web requests."""
    if not isinstance(article, dict):
        return "", "", False
    title = str(article.get("title") or "").strip()[:500]
    content = article.get("content") or {}
    if isinstance(content, str):
        body = content.strip()[:_MAX_CHARS]
        return title, body, bool(body)
    if not isinstance(content, dict):
        content = {}
    blocks = content.get("blocks")
    if not isinstance(blocks, list):
        body = str(content.get("markdown") or "").strip()[:_MAX_CHARS]
        return title, body, bool(body)
    entities = _entity_map(content.get("entityMap") or content.get("entity_map"))
    media = _media_lookup(article)
    output = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        kind = str(block.get("type") or "unstyled").lower()
        line = str(block.get("text") or "").strip()
        if kind == "atomic":
            output.extend(_atomic(block, entities, media))
            continue
        if not line:
            continue
        if kind in ("header-one", "header_1"):
            line = "# " + line
        elif kind in ("header-two", "header_2"):
            line = "## " + line
        elif kind in ("header-three", "header_3"):
            line = "### " + line
        elif kind == "unordered-list-item":
            line = "- " + line
        elif kind == "ordered-list-item":
            line = "1. " + line
        elif kind == "blockquote":
            line = "> " + line
        elif kind in ("code-block", "code"):
            line = (chr(96) * 3) + "\n" + line + "\n" + (chr(96) * 3)
        output.append(line)
    body = "\n\n".join(output).strip()[:_MAX_CHARS]
    if not body and isinstance(content.get("markdown"), str):
        body = content["markdown"].strip()[:_MAX_CHARS]
    return title, body, bool(body)


def looks_like_article_stub(post):
    """Short linked posts may need an additional FxTwitter Article lookup."""
    if not isinstance(post, dict) or post.get("article"):
        return False
    text = str(post.get("text") or "").strip()
    return len(text) < 380 and bool(re.search(
        r"https?://(?:t\.co/[A-Za-z0-9]+|(?:www\.)?(?:x|twitter)\.com/i/article/\d+)", text))
