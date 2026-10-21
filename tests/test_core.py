import asyncio
import concurrent.futures
import copy
import importlib
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_temporary = tempfile.TemporaryDirectory(prefix='zhijian-tests-', dir=Path(__file__).resolve().parents[1])
os.environ['ZHIJIAN_DATA_DIR'] = _temporary.name
os.environ['ZHIJIAN_DISABLE_MONITOR'] = '1'
import httpx
from fastapi.testclient import TestClient
import config
import database
from content_safety import sanitize_html, present_post
from crawlers.weibo import WeiboCrawler
from crawlers.base import AuthExpiredError, RateLimitedError
import fulltext
import image_proxy
import monitor
import main
from connections import credential_from_cookies, validate_credential

def response(data, code=200):
    return httpx.Response(code, json=data, request=httpx.Request('GET', 'https://m.weibo.cn/test'))

def blog(long=False):
    return {'id': 5352161488930068, 'bid':'Abc', 'text':'摘要 <a href="/status/5352161488930068">全文</a>' if long else '短文',
            'isLongText':long, 'user':{'screen_name':'作者'}, 'pics':[{'large':{'url':'https://wx1.sinaimg.cn/a.jpg'}}],
            'created_at':'Fri Oct 09 17:30:00 +0800 2026'}

def record(long=True):
    return dict(WeiboCrawler._parse_mblog(blog(long)), platform='weibo', author_id='123',
                post_uid='weibo:123:5352161488930068')

class CoreTests(unittest.TestCase):
    def setUp(self):
        config.save_config(copy.deepcopy(config.DEFAULT_CONFIG))
        database.init_db()
        with database._conn() as c:
            c.execute('DELETE FROM posts')
        fulltext._failures.clear()
        WeiboCrawler._cooldown_until = 0
        main.manager.jobs.clear()
        main.manager.results.clear()
        monitor.status.clear()

    def test_html_policy_and_escaped_text(self):
        raw='<img src="https://wx1.sinaimg.cn/a.jpg" onerror="alert(1)"><a href="javascript:alert(1)" onclick="x()">a</a><svg onload="x()"></svg><iframe src="x"></iframe>'
        clean=sanitize_html(raw)
        self.assertNotIn('onerror',clean)
        self.assertNotIn('javascript:',clean)
        self.assertNotIn('<svg',clean)
        self.assertNotIn('<iframe',clean)
        self.assertIn('/api/img?',clean)
        escaped=sanitize_html('&lt;img src=x onerror=alert(1)&gt;')
        self.assertNotIn('<img',escaped)
        self.assertIn('&lt;img',escaped)
        self.assertEqual(sanitize_html(clean),clean)
        self.assertIn('https://m.weibo.cn/status/1',sanitize_html('<a href="/status/1">全文</a>'))

    def test_longtext_and_retweet(self):
        mb=blog(True);mb['retweeted_status']=dict(blog(True),id=1234,user={'screen_name':'<img onerror=x>'})
        replies=[response({'ok':1,'data':mb}), response({'ok':1,'data':{'longTextContent':'完整主文'}}),
                 response({'ok':1,'data':{'longTextContent':'完整转发'}})]
        with patch.object(httpx.Client,'get',side_effect=replies):
            result=WeiboCrawler('SUB=secret').fetch_article_by_id(str(mb['id']))
        self.assertIn('完整主文',result['content_html'])
        self.assertIn('完整转发',result['content_html'])
        self.assertNotIn('<img onerror',result['content_html'])
        self.assertFalse(result['needs_fulltext'])
        self.assertIn('/detail/5352161488930068',result['url'])

    def test_fulltext_failure_preserves_summary_and_coalesces(self):
        p=record();database.insert_post(p);config.update(weibo_cookie='SUB=secret')
        with patch.object(WeiboCrawler,'fetch_article_by_id',side_effect=AuthExpiredError('expired')) as fetch:
            with self.assertRaises(AuthExpiredError):fulltext.complete_post(p['id'])
            with self.assertRaises(AuthExpiredError):fulltext.complete_post(p['id'])
            self.assertEqual(fetch.call_count,1)
        self.assertIn('摘要',database.get_post(p['id'])['content_html'])
        fulltext._failures.clear()
        original=dict(record(False),content_html='完整正文')
        def slow(_):time.sleep(.1);return original
        with patch.object(WeiboCrawler,'fetch_article_by_id',side_effect=slow) as fetch:
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
                results=list(pool.map(fulltext.complete_post,[p['id']]*5))
            self.assertEqual(fetch.call_count,1)
            self.assertTrue(all(r['content_html']=='完整正文' for r in results))
            fulltext.complete_post(p['id'])
            self.assertEqual(fetch.call_count,1)

    def test_legacy_fulltext_detection_and_migration(self):
        p=record();database.insert_post(p)
        with database._conn() as c:c.execute("UPDATE posts SET fulltext_state='unknown'")
        self.assertTrue(database.get_post(p['id'])['needs_fulltext'])
        self.assertEqual(database.query_posts()[0]['id'],p['id'])

    def test_rate_limit_cooldown(self):
        with patch.object(httpx.Client,'get',side_effect=[response({'ok':1,'data':blog(True)}),response({},429)]):
            with self.assertRaises(RateLimitedError):WeiboCrawler('SUB=x').fetch_article_by_id('5352161488930068')
        with patch.object(httpx.Client,'get') as fetch:
            with self.assertRaises(RateLimitedError):WeiboCrawler('SUB=x').fetch_article_by_id('5352161488930068')
            fetch.assert_not_called()

    def test_config_concurrent_partial_updates_and_renewal_lease(self):
        def writer(i):config.update(**{f'field{i}':i})
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(writer,range(40)))
        cfg=config.load_config();self.assertEqual(sum(cfg[f'field{i}'] for i in range(40)),780)
        config.update(weread_cookie='new')
        self.assertFalse(config.replace_credential('weread_cookie','old','stale'))
        self.assertEqual(config.load_config()['weread_cookie'],'new')
        self.assertFalse(list(Path(config.DATA_DIR).glob('*.tmp')))

    def test_atomic_write_failure_leaves_original(self):
        config.update(weibo_cookie='old')
        with patch('config.os.replace',side_effect=OSError('blocked')):
            with self.assertRaises(OSError):config.update(weibo_cookie='new')
        self.assertEqual(config.load_config()['weibo_cookie'],'old')

    def test_local_api_validation_and_secrets(self):
        with TestClient(main.app,base_url='http://127.0.0.1:8000') as c:
            self.assertEqual(c.put('/api/settings',json={'poll_interval_sec':'bad'}).status_code,422)
            self.assertEqual(c.put('/api/settings',json={'poll_interval_sec':-1}).status_code,422)
            self.assertEqual(c.put('/api/settings',json={'users':[{'platform':'bad','user_id':'x'}]}).status_code,422)
            self.assertEqual(c.put('/api/settings',json={'users':[{'platform':'weibo','user_id':'https://m.weibo.cn/u/123'}]}).status_code,200)
            self.assertEqual(config.load_config()['users'][0]['user_id'],'123')
            self.assertEqual(c.put('/api/settings',json={},headers={'Origin':'https://evil.example'}).status_code,403)
            self.assertEqual(c.get('/api/settings',headers={'Host':'evil.example'}).status_code,403)
            self.assertEqual(c.post('/api/connections/weibo/start',content='{}',headers={'Content-Type':'text/plain'}).status_code,415)
            config.update(weibo_cookie='SUB=secret')
            self.assertNotIn('secret',c.get('/api/settings').text)
            self.assertNotIn('secret',c.get('/api/connections').text)
            c.put('/api/settings',json={'poll_interval_sec':60})
            self.assertEqual(config.load_config()['weibo_cookie'],'SUB=secret')
            self.assertEqual(c.get('/api/posts?limit=-1').status_code,422)
            config.update(access_password='test')
            self.assertEqual(c.get('/api/posts').status_code,401)
            self.assertEqual(c.post('/api/login',json={'password':'test'}).status_code,200)
            self.assertEqual(c.get('/api/posts').status_code,200)

    def test_proxy_domain_and_private_dns(self):
        async def run():
            for url in ['http://127.0.0.1/','https://sinaimg.cn.evil.test/x','file:///etc/passwd','http://user:pass@sinaimg.cn/']:
                with self.assertRaises(Exception):await image_proxy.resolve_target(url)
            private=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]
            with patch('image_proxy.socket.getaddrinfo',return_value=private):
                with self.assertRaises(Exception):await image_proxy.resolve_target('https://wx1.sinaimg.cn/x')
            public=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))]
            with patch('image_proxy.socket.getaddrinfo',return_value=public):
                self.assertEqual((await image_proxy.resolve_target('https://wx1.sinaimg.cn/x'))[1],'93.184.216.34')
        asyncio.run(run())

    def test_proxy_redirect_type_limit_and_ip_pinning(self):
        class FakeResponse:
            def __init__(self,status,headers,body=b''):self.status_code=status;self.headers=headers;self.body=body
            async def __aenter__(self):return self
            async def __aexit__(self,*a):pass
            async def aiter_bytes(self):yield self.body
        class FakeClient:
            def __init__(self,replies):self.replies=iter(replies);self.urls=[]
            async def __aenter__(self):return self
            async def __aexit__(self,*a):pass
            def stream(self,method,url,**kw):self.urls.append((str(url),kw));return next(self.replies)
        async def run():
            public=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))]
            scenarios=[([FakeResponse(302,{'location':'http://127.0.0.1/private'})],400),
                       ([FakeResponse(200,{'content-type':'text/html'})],415),
                       ([FakeResponse(200,{'content-type':'image/png'},b'x'*20)],413)]
            for responses,code in scenarios:
                client=FakeClient(responses)
                with patch('image_proxy.httpx.AsyncClient',return_value=client),patch('image_proxy.socket.getaddrinfo',return_value=public),patch('image_proxy.MAX_SIZE',10):
                    try:await image_proxy.fetch_image('https://wx1.sinaimg.cn/a')
                    except main.HTTPException as e:self.assertEqual(e.status_code,code)
                    else:self.fail('proxy should reject')
                self.assertIn('93.184.216.34',client.urls[0][0]);self.assertEqual(client.urls[0][1]['extensions']['sni_hostname'],'wx1.sinaimg.cn')
        asyncio.run(run())

    def test_status_aggregation_and_durable_ids(self):
        config.update(users=[{'platform':'weibo','user_id':str(i),'enabled':True} for i in (1,2)],weibo_cookie='SUB=x')
        monitor.status.update({'weibo:1':{'state':'ok'},'weibo:2':{'state':'auth_error','message':'登录失效'}})
        self.assertEqual(monitor.get_status()['weibo']['state'],'partial_error')
        self.assertEqual(monitor.get_status()['zsxq']['state'],'disabled')
        with patch.object(WeiboCrawler,'fetch_posts',return_value=[WeiboCrawler._parse_mblog(blog(False))]),patch('monitor._backfill_profile'):
            out=monitor._run_one_user('weibo',{'user_id':'123'},config.load_config())
            self.assertIsInstance(out[0]['id'],int)
            self.assertEqual(monitor._run_one_user('weibo',{'user_id':'123'},config.load_config()),[])

    def test_connection_cookie_extraction_and_verification(self):
        self.assertEqual(credential_from_cookies('zsxq',[{'name':'zsxq_access_token','value':'secret'}]),'secret')
        self.assertEqual(credential_from_cookies('weibo',[]),'')
        with patch.object(httpx.Client,'get',return_value=response({'ok':1,'data':{'login':False}})):
            self.assertEqual(validate_credential('weibo','SUB=x')['state'],'auth_error')
        with patch.object(httpx.Client,'get',return_value=response({'ok':1,'data':{'login':True}})):
            self.assertEqual(validate_credential('weibo','SUB=x')['state'],'ok')

if __name__=='__main__':unittest.main()
