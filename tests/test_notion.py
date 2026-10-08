from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock
from src.notion import NotionPublisher, split_text, PART_SIZE


def test_created_page_is_remembered_before_content_upload_fails():
    notion = NotionPublisher('token', 'parent')
    notion.create_page = lambda title: ('page-id', 'https://www.notion.so/page-id')
    def fail_append(page_id, blocks):
        raise RuntimeError('simulated image upload/append error')
    notion.append = fail_append
    remembered = []
    archive = {'tweet_id': '123', 'source': 'https://x.com/i/status/123',
               'posts': [{'text': 'Saved post', 'author_username': 'test',
                          'created_at': '', 'url': 'https://x.com/i/status/123', 'media': []}]}
    from pytest import raises
    with TemporaryDirectory() as d:
        with raises(RuntimeError, match='页面已创建但填充失败'):
            notion.publish(archive, Path(d), on_created=remembered.append)
    assert remembered == ['https://www.notion.so/page-id']


def test_split_text_within_notion_limits():
    text='你好'*2500
    pieces=split_text(text)
    assert ''.join(pieces)==text
    assert max(map(len,pieces))<=1800


def test_small_file_upload_uses_notion_file_upload_flow():
    mock=Mock()
    notion=NotionPublisher('x','parent-id',session=mock)
    notion.max_size=5*1024*1024
    calls=[]
    def fake_call(method,path,**kwargs):
        calls.append((method,path,kwargs))
        if path=='/file_uploads':return {'id':'uploaded-id'}
        return {}
    notion.call=fake_call
    with TemporaryDirectory() as d:
        p=Path(d)/'video.mp4';p.write_bytes(b'small-video')
        assert notion.upload(p)=='uploaded-id'
    assert calls[0][2]['json']['mode']=='single_part'
    assert calls[1][1]=='/file_uploads/uploaded-id/send'
    assert len(calls)==2


def test_large_video_notion_multipart():
    notion=NotionPublisher('x','id')
    notion.max_size=40*1024*1024
    calls=[]
    def fake(method,path,**kwargs):
        calls.append((path,kwargs))
        if path=='/file_uploads':return {'id':'f'}
        return {}
    notion.call=fake
    with TemporaryDirectory() as d:
        p=Path(d)/'clip.mp4'
        with p.open('wb') as fh:
            fh.seek(21*1024*1024-1);fh.write(b'x')
        assert notion.upload(p)=='f'
    assert calls[0][1]['json']['mode']=='multi_part'
    assert calls[0][1]['json']['number_of_parts']==3
    assert [c[1]['data']['part_number'] for c in calls[1:4]]==['1','2','3']
    assert calls[4][0]=='/file_uploads/f/complete'