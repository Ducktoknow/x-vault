"""Two-stage video flow: metadata inspection and short-lived, one-use transfer tickets."""
import secrets
import threading
import time

from .archive import fetch_thread, media_items

_TICKETS = {}
_LOCK = threading.Lock()
_TTL_SECONDS = 300


def inspect_post(tweet_id):
    posts, has_thread, _payload = fetch_thread(tweet_id)
    expanded = []
    seen = set()
    for post in posts:
        if str(post.get("id")) not in seen:
            expanded.append(post)
            seen.add(str(post.get("id")))
        quote = post.get("quote")
        if isinstance(quote, dict) and quote.get("id") and str(quote["id"]) not in seen:
            expanded.append(quote)
            seen.add(str(quote["id"]))
    videos = [
        media for post in expanded
        for media in media_items(post)
        if isinstance(media, dict) and media.get("type") in ("video", "gif")
    ]
    first = posts[0]
    return {
        "tweet_id": tweet_id,
        "has_video": bool(videos),
        "video_count": len(videos),
        "post_count": len(posts),
        "thread_returned": has_thread,
        "text_preview": (first.get("text") or "")[:350],
        "author": (first.get("author") or {}).get("screen_name") or "",
    }


def issue_ticket(tweet_id):
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _LOCK:
        for key, (_id, deadline) in list(_TICKETS.items()):
            if deadline < now:
                del _TICKETS[key]
        if len(_TICKETS) > 100:
            raise RuntimeError("临时下载请求过多，请稍后重试")
        _TICKETS[token] = (tweet_id, now + _TTL_SECONDS)
    return token


def claim_ticket(token):
    with _LOCK:
        ticket = _TICKETS.pop(token, None)
    return ticket[0] if ticket and ticket[1] > time.time() else None