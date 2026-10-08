import importlib
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


def test_api_requires_token_and_deduplicates(monkeypatch):
    with TemporaryDirectory() as tmp:
        monkeypatch.setenv('DATA_DIR',tmp)
        monkeypatch.setenv('APP_TOKEN','abcdefghij1234567890abcdefghij')
        import src.app as app_mod
        importlib.reload(app_mod)
        monkeypatch.setattr(app_mod, 'inspect_post', lambda tweet_id: {'tweet_id': tweet_id, 'has_video': False, 'video_count': 0})
        with patch.object(app_mod,'queue_worker') as worker:
            # Avoid running network worker in tests
            import asyncio
            async def idle():
                await asyncio.sleep(300)
            worker.side_effect=idle
            with TestClient(app_mod.app) as client:
                assert client.get('/health').status_code==200
                assert client.get('/api/archives').status_code==401
                headers={'Authorization':'Bearer abcdefghij1234567890abcdefghij'}
                body={'url':'https://x.com/test/status/123456789'}
                a=client.post('/api/save',headers=headers,json=body).json()
                b=client.post('/api/save',headers=headers,json=body).json()
                assert a['new'] is True
                assert b['new'] is False
                assert client.get('/api/archives',headers=headers).json()['items'][0]['tweet_id']=='123456789'
                assert client.get('/media/123456789/x.mp4?key=wrong').status_code==404
                assert client.post('/api/save',headers=headers,json={'url':'http://127.0.0.1/status/123'}).status_code==400