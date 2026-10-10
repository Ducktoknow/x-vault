"""The save-for-later and download-to-Photos shortcuts must never be confused."""
import importlib
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient
from src.archive import archive_post
from src.notion import NotionPublisher

TOKEN='test-secret-token-01234567890123'
HEADERS={'Authorization':'Bearer '+TOKEN}
X='https://x.com/poster/status/738291'
TIKTOK='https://www.tiktok.com/@tester/video/1234567890123456789'

async def idle():
    import asyncio
    await asyncio.sleep(300)


def app_for(monkeypatch, root, *, notion=True):
    monkeypatch.setenv('DATA_DIR',root)
    monkeypatch.setenv('APP_TOKEN',TOKEN)
    monkeypatch.setenv('NOTION_TOKEN','fake-notion' if notion else '')
    monkeypatch.setenv('NOTION_PARENT_PAGE_ID','page-id' if notion else '')
    import src.app as mod
    importlib.reload(mod)
    mod.SHORTCUT_WAIT_SECONDS=0
    return mod


def test_x_video_bookmark_stores_cdn_direct_links_without_downloading(monkeypatch):
    from src import archive
    video={'id':'738291','text':'稍后观看的视频','author':{'screen_name':'poster'},'media':{
        'all':[{'type':'video','thumbnail_url':'https://pbs.twimg.com/cover.jpg','formats':[
            {'container':'mp4','height':720,'width':1280,'url':'https://video.twimg.com/video-720.mp4'},
            {'container':'mp4','height':360,'width':640,'url':'https://video.twimg.com/video-360.mp4'}]}]}}
    def forbidden(*args,**kwargs):
        raise AssertionError('bookmark must never download media')
    monkeypatch.setattr(archive,'safe_file_download',forbidden)
    monkeypatch.setattr(archive,'download_hls',forbidden)
    monkeypatch.setattr(archive,'fallback_yt_dlp',forbidden)
    with TemporaryDirectory() as d:
        result=archive_post('738291',Path(d),metadata_only=True,
                            fetcher=lambda x:([video],True,{}))
        media=result['posts'][0]['media'][0]
        assert media['direct_urls'][0]=='https://video.twimg.com/video-720.mp4'
        assert media['file'] is None
        pub=NotionPublisher('test','parent')
        pub.create_page=lambda title: ('page','https://notion.so/page')
        blocks=[]
        pub.append=lambda page,items:blocks.extend(items)
        pub.publish(result,Path(d),metadata_only=True)
        text=' '.join(b['paragraph']['rich_text'][0]['text']['content']
                      for b in blocks if b['type']=='paragraph')
        assert '视频解析直链' in text
        assert '可能过期' in text
        assert 'https://video.twimg.com/video-720.mp4' in [
            b['paragraph']['rich_text'][0]['text'].get('link',{}).get('url')
            for b in blocks if b['type']=='paragraph']


def test_tiktok_bookmark_writes_notion_without_download(monkeypatch):
    with TemporaryDirectory() as d:
        mod=app_for(monkeypatch,d)
        preview={'id':'1234567890123456789','title':'测试视频','author':'user',
                 'duration':15,'thumbnail':'https://p16.tiktokcdn.com/img.jpeg',
                 'video_urls':['https://v16-webapp.tiktokcdn.com/media.mp4?expire=123']}
        monkeypatch.setattr(mod.tiktok_video,'inspect_tiktok',lambda x:preview)
        monkeypatch.setattr(mod.tiktok_video,'download_tiktok',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('download called')))
        blocks=[]
        def fake_page(self,title): return 'pid','https://notion.so/bookmark'
        def fake_append(self,pid,data): blocks.extend(data)
        monkeypatch.setattr(mod.NotionPublisher,'create_page',fake_page)
        monkeypatch.setattr(mod.NotionPublisher,'append',fake_append)
        with patch.object(mod,'queue_worker',side_effect=idle):
            with TestClient(mod.app) as c:
                response=c.post('/api/shortcut/save',headers=HEADERS,json={'url':TIKTOK})
                assert response.status_code==200
                assert response.json()['status']=='complete'
                assert response.json()['notion_url']=='https://notion.so/bookmark'
                assert c.get('/api/archives',headers=HEADERS).json()['items']==[]
        combined=' '.join(b['paragraph']['rich_text'][0]['text']['content'] for b in blocks if b['type']=='paragraph')
        assert '视频说明' in combined and '解析视频直链' in combined and '可能过期' in combined
        assert TIKTOK in [b['paragraph']['rich_text'][0]['text'].get('link',{}).get('url')
                          for b in blocks if b['type']=='paragraph']
        assert preview['video_urls'][0] in [b['paragraph']['rich_text'][0]['text'].get('link',{}).get('url')
                                            for b in blocks if b['type']=='paragraph']


def test_downloader_refuses_text_only_x_and_does_not_archive(monkeypatch):
    with TemporaryDirectory() as d:
        mod=app_for(monkeypatch,d,notion=False)
        monkeypatch.setattr(mod,'inspect_post',lambda x:{'has_video':False})
        with patch.object(mod,'queue_worker',side_effect=idle):
            with TestClient(mod.app) as c:
                r=c.post('/api/shortcut/save',headers=HEADERS,json={'url':X,'video_action':'download_only'})
                assert r.json()['status']=='failed'
                assert c.get('/api/archives',headers=HEADERS).json()['items']==[]


def test_two_shortcuts_import_questions_and_behaviors(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'ios'))
    from build_shortcut_later import build_later_shortcut
    from build_shortcut_video import build_video_shortcut
    later=build_later_shortcut()
    photos=build_video_shortcut()
    assert len(later['WFWorkflowActions'])==4
    assert len(photos['WFWorkflowActions'])==20
    assert '稍后看' in later['WFWorkflowName']
    assert '下载视频' in photos['WFWorkflowName']
    keys=later['WFWorkflowActions'][1]['WFWorkflowActionParameters']['WFJSONValues']['Value']['WFDictionaryFieldValueItems']
    assert [x['WFKey']['Value']['string'] for x in keys]==['url']
    keys=photos['WFWorkflowActions'][1]['WFWorkflowActionParameters']['WFJSONValues']['Value']['WFDictionaryFieldValueItems']
    assert keys[1]['WFValue']['Value']['string']=='download_only'
    for wf in (later,photos):
        assert {(x['ActionIndex'],x['ParameterKey']) for x in wf['WFWorkflowImportQuestions']}=={
            (0,'WFTextActionText'),(1,'WFURL')}