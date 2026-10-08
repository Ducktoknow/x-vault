import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest
from src.archive import (parse_post_url, normalize_thread, best_video_urls,
                         safe_file_download, archive_post, valid_cdn_url)


def test_x_urls():
    assert parse_post_url('https://x.com/test/status/123456?a=1')[0] == '123456'
    assert parse_post_url('https://mobile.twitter.com/u/status/12/photo/1')[0] == '12'
    for url in ('https://evil.tld/u/status/123','https://x.com.attacker.tld/u/status/99',
                'http://127.0.0.1/u/status/34', 'file:///etc/passwd',
                'https://x.com/user', 'https://x.com/user/status/notanumber'):
        with pytest.raises(ValueError):
            parse_post_url(url)


def test_thread_dedup_order():
    a={'id':'99','type':'status','created_timestamp':2,'text':'second'}
    b={'id':'98','type':'status','created_timestamp':1,'text':'first'}
    posts, is_thread=normalize_thread({'status':a,'thread':[a,b,{'type':'tombstone','id':'10'}]})
    assert [p['id'] for p in posts] == ['98','99']
    assert is_thread


def test_video_select_highest_resolution():
    entry={"formats":[
        {"container":"mp4","url":"https://video.twimg.com/a320.mp4","bitrate":900000,"height":320,"width":600},
        {"container":"m3u8","url":"https://video.twimg.com/a.m3u8"},
        {"container":"mp4","url":"https://video.twimg.com/a1080.mp4","bitrate":800000,"height":1080,"width":1920}
    ]}
    files, hls = best_video_urls(entry)
    assert files[0]['url'].endswith('a1080.mp4')
    assert len(hls)==1


def test_trusted_media_domains():
    assert valid_cdn_url('https://pbs.twimg.com/media/a.jpg?name=orig')
    assert not valid_cdn_url('https://pbs.twimg.com.evil.net/steal')
    assert not valid_cdn_url('http://127.0.0.1:8000/x')


class FakeResponse:
    status_code=200
    headers={"Content-Length":"12"}
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def raise_for_status(self):pass
    def iter_content(self,chunk_size):yield b'0123456789ab'

class FakeSession:
    def get(self,*args,**kwargs):return FakeResponse()


def test_download_is_real_file_not_link():
    with TemporaryDirectory() as tmp:
        p=Path(tmp)/'m.mp4'
        safe_file_download('https://video.twimg.com/video.mp4',p,128,session=FakeSession())
        assert p.read_bytes()==b'0123456789ab'


def test_archive_records_media_failure_and_saves_text():
    tweet={'id':'123','type':'status','text':'test message','created_at':'2026-10-08',
           'author':{'screen_name':'test'},
           'media':{'all':[{'type':'video','url':'https://invalid.example.com/a.mp4'}]}}
    with TemporaryDirectory() as tmp:
        archive=archive_post('123',Path(tmp),fetcher=lambda _id:([tweet],True,{}),use_ytdlp_fallback=False)
        assert archive['posts'][0]['text']=='test message'
        assert archive['posts'][0]['media'][0]['file'] is None
        assert archive['errors']
        assert (Path(tmp)/'archives/123/archive.json').exists()
        assert 'test message' in (Path(tmp)/'archives/123/archive.md').read_text()


def test_m3u8_default_must_not_be_stored_as_mp4():
    mp4,hls=best_video_urls({'url':'https://video.twimg.com/v/playlist.m3u8','formats':[]})
    assert not mp4
    assert len(hls)==1


def test_real_video_file_validation_and_archive(monkeypatch):
    import shutil, subprocess
    from src import archive as mod
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('ffmpeg not installed locally')
    with TemporaryDirectory() as tmp:
        original=Path(tmp)/'original.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=160x90:r=5',
                        '-t','0.5','-c:v','mpeg4','-y',str(original)],check=True)
        post={'id':'333','text':'Video post','created_at':'2026-10-08',
              'author':{'screen_name':'tester'},'media':{'all':[{'type':'video',
              'formats':[{'container':'mp4','url':'https://video.twimg.com/s.mp4','width':160,'height':90}]}]}}
        def fake_down(url,dest,limit,**kwargs):
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(original,dest)
            return dest
        monkeypatch.setattr(mod,'safe_file_download',fake_down)
        result=mod.archive_post('333',Path(tmp),fetcher=lambda _:([post],True,{}))
        m=result['posts'][0]['media'][0]
        assert m['file'] and m['file'].endswith('.mp4')
        assert (Path(tmp)/'archives/333'/m['file']).stat().st_size > 0
        assert result['errors']==[]