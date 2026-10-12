"""Deterministic reading blocks; preserve wording, line order and code whitespace."""
import re

IMAGE_RE = re.compile(r"!\[([^\]\r\n]{0,200})\]\((https://[^)\s]+)\)")


def text_block(kind, text):
    return {"object": "block", "type": kind, kind: {"rich_text": [
        {"type": "text", "text": {"content": text}}]}}


def chunks(text, limit=1800):
    """Prefer sentence/whitespace boundaries; never drop source characters."""
    while len(text) > limit:
        end = max(text.rfind(mark, 0, limit) + 1 for mark in ("。", "！", "？", ". ", "\n", " "))
        if end < limit // 2:
            end = limit
        yield text[:end]
        text = text[end:]
    if text:
        yield text


def reading_blocks(text, *, image_block=None):
    """Small Markdown subset with native headings, lists, quotes and code.

    Image conversion only occurs outside fenced code. Unrecognized Markdown
    remains literal text; no AI rewriting or invented headings.
    """
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    blocks, pending, code = [], [], []
    fence = None
    opening = ""

    def emit(kind, value):
        blocks.extend(text_block(kind, chunk) for chunk in chunks(value))

    def flush():
        if pending:
            emit("paragraph", "\n".join(pending).strip())
            pending.clear()

    for line in text.split("\n"):
        marker = re.match(r"^\s*(\x60{3,}|~{3,})(.*)$", line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                value = "\n".join(code)
                for chunk in chunks(value):
                    block = text_block("code", chunk)
                    block["code"]["language"] = "plain text"
                    blocks.append(block)
                fence, code = None, []
            else:
                code.append(line)
            continue
        if marker:
            flush()
            fence = marker[1]
            opening = line
            code = []
            continue
        if not line.strip():
            flush()
            continue
        heading = re.match(r"^\s*(#{1,6})\s+(.+)$", line)
        bullet = re.match(r"^\s*[-*+]\s+(.+)$", line)
        number = re.match(r"^\s*\d+[.)]\s+(.+)$", line)
        quote = re.match(r"^\s*>\s?(.*)$", line)
        if heading or bullet or number or quote:
            flush()
            if heading:
                emit("heading_" + str(min(len(heading[1]), 3)), heading[2].strip())
            elif bullet:
                emit("bulleted_list_item", bullet[1])
            elif number:
                emit("numbered_list_item", number[1])
            else:
                emit("quote", quote[1])
            continue
        if image_block:
            current = 0
            for match in IMAGE_RE.finditer(line):
                if line[current:match.start()].strip():
                    pending.append(line[current:match.start()])
                flush()
                blocks.append(image_block(match[2], match[1] or "文章配图"))
                current = match.end()
            if current:
                if line[current:].strip():
                    pending.append(line[current:])
                continue
        pending.append(line.rstrip())
    if fence:
        # An incomplete fence is source text, not permission to discard it.
        emit("paragraph", opening + "\n" + "\n".join(code))
    flush()
    return blocks


def author_label(post):
    name = str(post.get("author_name") or "").strip()
    username = str(post.get("author_username") or "").strip().lstrip("@")
    if name and username:
        return f"{name} (@{username})"
    return name or ("@" + username if username else "未知")


def thread_members(posts):
    """Count the available self-thread, excluding quoted posts and Articles."""
    main = [p for p in posts if not p.get("is_quote")]
    if len(main) < 2 or any(p.get("is_article") or p.get("article_title") for p in main):
        return []
    authors = {str(p.get("author_username") or "").casefold() for p in main}
    # Missing identities aren't evidence of a self-thread.
    return main if len(authors) == 1 and "" not in authors else []
