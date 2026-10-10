"""Regression coverage for X Articles: full body, Draft.js blocks and Notion."""
from pathlib import Path
from tempfile import TemporaryDirectory

from src.article import article_markdown
from src.archive import archive_post, fetch_thread
from src.notion import NotionPublisher


def article_fixture():
    return {
        "id": "2108132811635032316", "type": "status",
        "text": "https://t.co/placeholder",
        "author": {"screen_name": "kingao476942"}, "media": {},
        "article": {
            "title": "文章正文示例", "preview_text": "短摘要",
            "cover_media": {"media_info": {"original_img_url": "https://pbs.twimg.com/media/cover.jpg"}},
            "content": {
                "blocks": [
                    {"type": "header-two", "text": "第一章"},
                    {"type": "unstyled", "text": "长文内容：" + "这是正文。" * 550},
                    {"type": "unordered-list-item", "text": "文章第一点"},
                    {"type": "atomic", "text": "", "entityRanges": [{"key": 0}]},
                    {"type": "atomic", "text": "", "entityRanges": [{"key": 1}]},
                ],
                "entityMap": {
                    "0": {"type": "MARKDOWN", "data": {"markdown": "`code` 示例"}},
                    "1": {"type": "TWEET", "data": {"tweetId": "123456"}},
                },
            },
        },
    }


def test_article_full_body_is_archived_and_synced_to_notion():
    tweet = article_fixture()
    _, body, complete = article_markdown(tweet["article"])
    assert complete and "第一章" in body and "长文内容" in body
    with TemporaryDirectory() as tmp:
        archive = archive_post(tweet["id"], Path(tmp),
                               fetcher=lambda _: ([tweet], True, {}),
                               use_ytdlp_fallback=False)
        post = archive["posts"][0]
        assert post["text"].startswith("文章正文示例\n\n## 第一章")
        assert "引用帖子：https://x.com/i/status/123456" in post["text"]
        assert post["article_title"] == "文章正文示例"
        assert post["article_cover_url"].startswith("https://pbs.twimg.com")
        assert archive["errors"] == []
        assert "长文内容" in (Path(tmp) / "archives" / tweet["id"] / "archive.md").read_text()
        publisher = NotionPublisher("fake", "parent-id")
        created = []
        publisher.create_page = lambda title: (created.append(title) or "page-id",
                                               "https://notion.so/page-id")
        blocks = []
        publisher.append = lambda page, items: blocks.extend(items)
        publisher._article_image_block = lambda url, warnings, caption="文章配图": {
            "object": "block", "type": "image", "image": {
                "type": "external", "external": {"url": url}}}
        publisher.publish(archive, Path(tmp), metadata_only=False)
        assert "文章正文示例" in created[0]
        combined = "".join(
            rich["text"]["content"]
            for b in blocks if b["type"] == "paragraph"
            for rich in b["paragraph"]["rich_text"]
        )
        assert "这是正文" in combined and "第一章" in combined
        assert any(b["type"] == "image" for b in blocks)


def test_article_with_only_preview_reports_incomplete():
    tweet = article_fixture()
    tweet["article"] = {"title": "只有标题", "preview_text": "只有摘要"}
    with TemporaryDirectory() as tmp:
        result = archive_post(tweet["id"], Path(tmp),
                              fetcher=lambda _: ([tweet], True, {}))
        assert "完整正文未返回" in result["errors"][0]
        assert result["posts"][0]["text"] == "只有标题\n\n只有摘要"


class _FakeResponse:
    def __init__(self, data):
        self.data = data
    def raise_for_status(self):
        return None
    def json(self):
        return self.data


class _ArticleSession:
    def __init__(self, empty=False):
        self.calls = []
        self.empty = empty
    def get(self, url, **kwargs):
        self.calls.append(url)
        if "/2/thread/" in url:
            tweet = article_fixture()
            tweet.pop("article")
            if self.empty:
                tweet["text"] = ""
            return _FakeResponse({"code": 200, "status": tweet, "thread": []})
        if "/kingao476942/status/" in url:
            return _FakeResponse({"code": 200, "tweet": article_fixture()})
        raise AssertionError("Unexpected API request " + url)


def test_article_link_only_triggers_detail_api():
    session = _ArticleSession()
    posts, _, _ = fetch_thread("2108132811635032316", session=session)
    assert len(session.calls) == 2
    assert posts[0]["article"]["content"]["blocks"]


def test_blank_article_wrapper_triggers_detail_api():
    session = _ArticleSession(empty=True)
    posts, _, _ = fetch_thread("2108132811635032316", session=session)
    assert len(session.calls) == 2
    assert posts[0]["article"]["title"] == "文章正文示例"


def test_regular_post_does_not_make_extra_request():
    session = _ArticleSession()
    def get(url, **kwargs):
        assert "/2/thread/" in url
        return _FakeResponse({"code": 200, "status": {
            "id": "123", "text": "普通帖子正文", "type": "status",
            "author": {"screen_name": "normal"}}, "thread": []})
    session.get = get
    posts, _, _ = fetch_thread("123", session=session)
    assert posts[0]["text"] == "普通帖子正文"


def test_article_atomic_image_is_preserved():
    article = {
        "title": "有配图",
        "media_entities": [{
            "media_id": "7",
            "media_info": {"original_img_url": "https://pbs.twimg.com/media/photo.jpg"}
        }],
        "content": {"blocks": [{"type": "atomic", "text": "",
                                "entityRanges": [{"key": 0}]}],
                    "entityMap": [{"key": 0, "value": {
                        "type": "MEDIA", "data": {"mediaItems": [{"mediaId": "7"}]}
                    }}]}
    }
    _, body, complete = article_markdown(article)
    assert complete and "![文章配图](https://pbs.twimg.com/media/photo.jpg)" in body


def test_blank_post_without_article_object_is_not_mistaken_for_complete():
    tweet = article_fixture()
    tweet.pop("article")
    tweet["text"] = ""
    with TemporaryDirectory() as tmp:
        result = archive_post(tweet["id"], Path(tmp),
                              fetcher=lambda _: ([tweet], True, {}))
        assert any("疑似 X Article" in error for error in result["errors"])


def test_link_caption_does_not_force_an_extra_article_request():
    from src.article import looks_like_article_stub
    assert not looks_like_article_stub({"text": "随便分享一个网站 https://t.co/abc123"})
    assert looks_like_article_stub({"text": "https://t.co/abc123"})
    assert looks_like_article_stub({"text": "新文章来了 https://t.co/abc123"})


def test_blank_video_with_media_does_not_trigger_article_lookup():
    from src.article import looks_like_article_stub
    post = {"text": "", "media": {"all": [
        {"type": "video", "url": "https://video.twimg.com/clip.mp4"}]}}
    assert not looks_like_article_stub(post)


def test_article_embedded_images_are_real_notion_blocks_in_order():
    from src.notion import NotionPublisher
    pub = NotionPublisher("fake", "parent")
    calls = []
    def image_block(url, warnings, caption="文章配图"):
        calls.append(url)
        return {"object": "block", "type": "image", "image": {
            "type": "external", "external": {"url": url}}}
    pub._article_image_block = image_block
    text = ("第一段\n\n![文章配图](https://pbs.twimg.com/media/first.jpg)\n\n"
            "第二段\n\n![文章配图](https://pbs.twimg.com/media/second.jpg)\n\n最后一段")
    warnings = []
    blocks = pub._article_body_blocks(text, warnings)
    assert [b["type"] for b in blocks] == ["paragraph", "image", "paragraph", "image", "paragraph"]
    assert [b["paragraph"]["rich_text"][0]["text"]["content"] for b in blocks if b["type"] == "paragraph"] == ["第一段\n\n", "\n\n第二段\n\n", "\n\n最后一段"]
    assert len(calls) == 2 and not warnings


def test_article_image_large_uses_external_and_no_upload(monkeypatch):
    from src.notion import NotionPublisher, ARTICLE_IMAGE_UPLOAD_MAX_BYTES
    from src import notion as mod
    pub = NotionPublisher("fake", "parent")
    monkeypatch.setattr(pub, "max_upload_size", lambda: 5*1024*1024)
    monkeypatch.setattr(pub, "upload", lambda *_: (_ for _ in ()).throw(AssertionError("must not upload large image")))
    def too_large(url, dest, max_bytes, **kwargs):
        assert max_bytes == ARTICLE_IMAGE_UPLOAD_MAX_BYTES
        # safe_file_download enforces this even when Content-Length is absent.
        raise ValueError("media file is too large")
    monkeypatch.setattr(mod, "safe_file_download", too_large)
    warnings=[]
    url="https://pbs.twimg.com/media/real-large.jpg"
    result=pub._article_image_block(url,warnings)
    assert result["image"]["type"] == "external"
    assert result["image"]["external"]["url"] == url
    assert not warnings


def test_article_image_small_is_uploaded_to_notion(monkeypatch):
    from src.notion import NotionPublisher, ARTICLE_IMAGE_UPLOAD_MAX_BYTES
    from src import notion as mod
    pub=NotionPublisher("fake","parent")
    monkeypatch.setattr(pub,"max_upload_size",lambda:5*1024*1024)
    def fake_small(url,dest,max_bytes,**kwargs):
        assert max_bytes==ARTICLE_IMAGE_UPLOAD_MAX_BYTES
        dest.write_bytes(b"\xff\xd8\xff" + b"a" * 1024)
    monkeypatch.setattr(mod,"safe_file_download",fake_small)
    monkeypatch.setattr(mod,"verify_image_file",lambda p: None)
    calls=[]
    def fake_upload(path):
        calls.append(path.read_bytes())
        return "uploaded-id"
    monkeypatch.setattr(pub,"upload",fake_upload)
    warnings=[]
    result=pub._article_image_block("https://pbs.twimg.com/media/photo.jpg",warnings)
    assert result["image"]["type"] == "file_upload"
    assert result["image"]["file_upload"]["id"] == "uploaded-id"
    assert len(calls)==1 and not warnings


def test_article_image_workspace_limit_used_as_bound(monkeypatch):
    from src.notion import NotionPublisher
    from src import notion as mod
    pub=NotionPublisher("fake","parent")
    monkeypatch.setattr(pub,"max_upload_size",lambda: 2*1024*1024)
    def too_large(url,dest,max_bytes,**kwargs):
        assert max_bytes == 2*1024*1024
        raise ValueError("exceeds Notion workspace limit")
    monkeypatch.setattr(mod,"safe_file_download",too_large)
    warnings=[]
    result=pub._article_image_block("https://pbs.twimg.com/media/test.jpg",warnings)
    assert result["image"]["type"]=="external"
    assert not warnings


def test_article_image_upload_error_falls_back_to_original_link(monkeypatch):
    from src.notion import NotionPublisher
    from src import notion as mod
    pub=NotionPublisher("fake","parent")
    monkeypatch.setattr(pub,"max_upload_size",lambda: 5*1024*1024)
    monkeypatch.setattr(mod,"safe_file_download",lambda url,dest,max_bytes,**kw: dest.write_bytes(b"\xff\xd8\xffabc"))
    monkeypatch.setattr(mod,"verify_image_file",lambda p: None)
    monkeypatch.setattr(pub,"upload",lambda path: (_ for _ in ()).throw(RuntimeError("Notion upload unavailable")))
    result=pub._article_image_block("https://pbs.twimg.com/media/test.jpg",[])
    assert result["image"]["type"]=="external"


def test_article_image_invalid_external_falls_back_to_text():
    from src.notion import NotionPublisher
    pub = NotionPublisher("fake", "parent")
    warnings = []
    block = pub._article_image_block("https://evil.example/path.jpg", warnings)
    assert block["type"] == "paragraph"
    assert warnings