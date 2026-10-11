"""V2 database initialization, no-AI metadata, storage and route regression."""
import importlib
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient
from src import notion_database
from src.notion import NotionPublisher

TOKEN = 'test-secret-token-01234567890123'
AUTH = {'Authorization': 'Bearer '+TOKEN}
PARENT = '1234567890abcdef1234567890abcdef'
DATABASE = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
SOURCE = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'


async def idle():
    import asyncio
    await asyncio.sleep(300)


def init(monkeypatch, tmp):
    monkeypatch.setenv('DATA_DIR', str(tmp))
    monkeypatch.setenv('APP_TOKEN', TOKEN)
    monkeypatch.setenv('NOTION_TOKEN', '')
    monkeypatch.setenv('NOTION_PARENT_PAGE_ID', '')
    import src.app as app
    importlib.reload(app)
    return app


def test_v2_database_schema_no_ai_dependency():
    fields=notion_database.fields()
    assert fields['标题']=={'type': 'title', 'title': {}}
    assert fields['阅读状态']['select']['options']==[
        {'name':'待读'},{'name':'已读'},{'name':'已实践'}]
    assert fields['AI 摘要']=={'type': 'rich_text', 'rich_text':{}}
    assert fields['原文链接']=={'type': 'url', 'url':{}}
    assert 'AI' not in notion_database.page_properties('Hello','X','文章','https://x.com/i/status/123')
    assert notion_database.page_properties('Title','X','帖子','https://x.com/i/status/123')['阅读状态']=={'select':{'name':'待读'}}


def test_notions_create_database_payload_and_datasource_fallback():
    calls=[]
    class FakePublisher:
        def __init__(self, token, parent):
            assert token=='test' and parent==PARENT
        def call(self, method, path, **kwargs):
            calls.append((method,path,kwargs))
            if path=='/databases' and method=='POST':
                return {'id':DATABASE,'url':'https://www.notion.so/'+DATABASE}
            if path=='/views':
                assert kwargs['json']['data_source_id']==SOURCE
                return {'object':'view','id':'view-id'}
            return {'data_sources':[{'id':SOURCE}]}
    result=notion_database.create('test',PARENT,publisher_factory=FakePublisher)
    assert result['data_source_id']==SOURCE
    assert calls[0][1]=='/databases'
    body=calls[0][2]['json']
    assert body['parent']=={'type':'page_id','page_id':PARENT}
    assert body['initial_data_source']['properties']['标题']=={'type':'title','title':{}}
    assert calls[1][1]=='/databases/'+DATABASE
    assert [x[2]['json']['name'] for x in calls if x[1]=='/views']==['待阅读','待实践','已读归档']


def test_create_page_parent_is_datasource_and_manual_reading_status():
    p=NotionPublisher('notion-secret',PARENT,data_source_id=SOURCE)
    calls=[]
    def call(method,path,**kwargs):
        calls.append(kwargs['json'])
        return {'id':'page-1','url':'https://notion.so/page-1'}
    p.call=call
    url=p.create_collection_page('文章标题','https://x.com/i/status/123','X','文章')
    assert url[1]=='https://notion.so/page-1'
    assert calls[0]['parent']=={'type':'data_source_id','data_source_id':SOURCE}
    assert calls[0]['properties']['阅读状态']=={'select':{'name':'待读'}}
    assert calls[0]['properties']['内容类型']=={'select':{'name':'文章'}}
    assert 'AI 摘要' not in calls[0]['properties']


def test_initialize_twice_only_creates_one_db_and_enables_writer(monkeypatch):
    with TemporaryDirectory() as d:
        app=init(monkeypatch,d)
        calls=[]
        def create(token,parent,**kwargs):
            assert token=='notion-user-token' and parent==PARENT
            calls.append('create')
            return {'database_id':DATABASE,'data_source_id':SOURCE,
                    'database_url':'https://www.notion.so/'+DATABASE}
        with patch.object(app,'queue_worker',side_effect=idle), patch.object(
             app.notion_config,'validate_page',return_value='Parent'), patch.object(
             app.notion_database,'create',side_effect=create):
            with TestClient(app.app) as client:
                assert client.post('/api/notion/database/initialize',headers=AUTH).status_code==409
                configured=client.post('/api/notion/configure',headers=AUTH,
                     json={'token':'notion-user-token','page_url':PARENT})
                assert configured.status_code==200
                assert client.get('/api/notion/connection',headers=AUTH).json()['database_ready'] is False
                first=client.post('/api/notion/database/initialize',headers=AUTH)
                assert first.status_code==200
                assert first.json()['created'] is True
                second=client.post('/api/notion/database/initialize',headers=AUTH)
                assert second.status_code==200
                assert second.json()['created'] is False
                assert calls==['create']
                assert client.get('/api/notion/connection',headers=AUTH).json()['database_ready'] is True
                assert app.collection_data_source_id()==SOURCE
                assert client.get('/api/notion/database/initialize',headers=AUTH).status_code==405
                with app.conn() as db:
                    record=app.notion_database.read(db)
                    assert record['database_id']==DATABASE
                disconnected=client.post('/api/notion/disconnect',headers=AUTH)
                assert disconnected.status_code==200
                assert app.collection_data_source_id() is None


def test_notions_parent_change_invalidates_collection(monkeypatch):
    with TemporaryDirectory() as d:
        app=init(monkeypatch,d)
        with patch.object(app,'queue_worker',side_effect=idle), patch.object(
             app.notion_config,'validate_page',return_value='Parent'), patch.object(
             app.notion_database,'create',return_value={
                  'database_id':DATABASE,'data_source_id':SOURCE,
                  'database_url':'https://www.notion.so/'+DATABASE}):
            with TestClient(app.app) as client:
                client.post('/api/notion/configure',headers=AUTH,
                            json={'token':'notion-secret-token','page_url':PARENT})
                client.post('/api/notion/database/initialize',headers=AUTH)
                different='cccccccccccccccccccccccccccccccc'
                client.post('/api/notion/configure',headers=AUTH,
                            json={'token':'notion-secret-token','page_url':different})
                assert app.collection_data_source_id() is None


def test_changed_integration_token_clears_stale_datasource(monkeypatch):
    with TemporaryDirectory() as d:
        app=init(monkeypatch,d)
        with patch.object(app,'queue_worker',side_effect=idle), patch.object(
             app.notion_config,'validate_page',return_value='Parent'), patch.object(
             app.notion_database,'create',return_value={
                  'database_id':DATABASE,'data_source_id':SOURCE,
                  'database_url':'https://www.notion.so/'+DATABASE}):
            with TestClient(app.app) as client:
                client.post('/api/notion/configure',headers=AUTH,
                    json={'token':'notion-user-secret-A','page_url':PARENT})
                client.post('/api/notion/database/initialize',headers=AUTH)
                assert app.collection_data_source_id()==SOURCE
                client.post('/api/notion/configure',headers=AUTH,
                    json={'token':'notion-user-secret-B','page_url':PARENT})
                assert app.collection_data_source_id() is None


def test_tiktok_collection_writer_still_preserves_original_video_link():
    p=NotionPublisher('test',PARENT,data_source_id=SOURCE)
    p.create_collection_page=lambda title,url,source,kind:(
        (title,url,source,kind), 'https://notion.so/article')
    actual=[]
    p.append=lambda page,blocks: actual.extend(blocks)
    p.publish_tiktok('https://www.tiktok.com/@x/video/123',{
        'title':'Demo','author':'x','duration':10,'thumbnail':None,'video_urls':[]})
    assert any('原视频' in str(b) for b in actual)


def test_notions_view_failure_does_not_break_database_bootstrap():
    class BrokenViews:
        def __init__(self, token, parent): pass
        def call(self, method, path, **kwargs):
            if path=='/databases':
                return {'id':DATABASE,'data_sources':[{'id':SOURCE}]}
            if path=='/views':
                raise RuntimeError('Notion update capability missing')
            raise AssertionError(path)
    result=notion_database.create('test',PARENT,publisher_factory=BrokenViews)
    assert result['data_source_id']==SOURCE
    assert result['view_warnings']==['待阅读','待实践','已读归档']


def test_reconnect_existing_database_checks_parent_and_schema(monkeypatch):
    from src import notion_config
    import sqlite3
    with TemporaryDirectory() as root:
        dbpath=root+'/settings.sqlite3'
        def conn():
            connection=sqlite3.connect(dbpath)
            connection.row_factory=sqlite3.Row
            return connection
        with conn() as db:
            notion_database.init_table(db)
        calls=[]
        class Notion:
            def __init__(self,token,parent):
                assert token=='key' and parent==PARENT
            def call(self,method,path,**kwargs):
                calls.append(path)
                if path.startswith('/databases/'):
                    return {'object':'database','id':DATABASE,
                            'url':'https://www.notion.so/'+DATABASE,
                            'parent':{'type':'page_id','page_id':PARENT},
                            'data_sources':[{'id':SOURCE}]}
                if path.startswith('/data_sources/'):
                    return {'object':'data_source','properties':{
                        '标题':{'type':'title'},'原文链接':{'type':'url'},
                        '阅读状态':{'type':'select'},'来源':{'type':'select'},
                        '内容类型':{'type':'select'}}}
                raise AssertionError(path)
        value=notion_database.reconnect(conn,'key',PARENT,DATABASE,publisher_factory=Notion)
        assert value['data_source_id']==SOURCE
        assert calls==['/databases/'+DATABASE.replace('-', ''),'/data_sources/'+SOURCE]
        again=notion_database.reconnect(conn,'key',PARENT,DATABASE,publisher_factory=Notion)
        assert again['database_id']==DATABASE.replace('-', '')
        with conn() as db:
            assert db.execute('SELECT COUNT(*) FROM notion_collection').fetchone()[0]==1

        class WrongParent(Notion):
            def call(self,method,path,**kwargs):
                out=super().call(method,path,**kwargs)
                if path.startswith('/databases/'):
                    out['parent']['page_id']='cccccccccccccccccccccccccccccccc'
                return out
        import pytest
        with pytest.raises(notion_config.NotionSetupError,match='父页面'):
            notion_database.reconnect(conn,'key',PARENT,DATABASE,publisher_factory=WrongParent)


def test_reconnect_route_requires_existing_notion_configuration(monkeypatch):
    with TemporaryDirectory() as root:
        app=init(monkeypatch,root)
        with patch.object(app,'queue_worker',side_effect=idle):
            with TestClient(app.app) as client:
                url='https://www.notion.so/'+DATABASE
                assert client.post('/api/notion/database/reconnect',
                    json={'database_url':url}).status_code==401
                response=client.post('/api/notion/database/reconnect',headers=AUTH,
                    json={'database_url':url})
                assert response.status_code==409


def test_db_writer_end_to_end_preserves_article_body_and_properties():
    archive={'tweet_id':'123','source':'https://x.com/i/status/123','errors':[],
             'posts':[{'id':'123','text':'X 长文正文','is_article':True,
                       'article_title':'这是文章','author_username':'writer',
                       'url':'https://x.com/i/status/123','created_at':'',
                       'media':[]}]}
    pub=NotionPublisher('token',PARENT,data_source_id=SOURCE)
    calls=[]
    def fake_call(method,path,**kwargs):
        calls.append((method,path,kwargs))
        if path=='/pages':return {'id':'new-page-id','url':'https://notion.so/new-page'}
        return {}
    pub.call=fake_call
    from pathlib import Path
    with TemporaryDirectory() as root:
        result,warnings=pub.publish(archive,Path(root))
    assert result=='https://notion.so/new-page' and warnings==[]
    create=calls[0][2]['json']
    assert create['parent']=={'type':'data_source_id','data_source_id':SOURCE}
    assert create['properties']['内容类型']=={'select':{'name':'文章'}}
    assert create['properties']['阅读状态']=={'select':{'name':'待读'}}
    assert any('X 长文正文' in str(call) for call in calls if call[1].startswith('/blocks/'))