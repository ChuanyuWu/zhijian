import asyncio
import json
import unittest
from unittest.mock import patch
import test_core
from config import update
from settings_schema import normalize_id, User
from discovery import discover, search_weibo
from crawlers.weibo import WeiboCrawler
from fastapi.testclient import TestClient
import main
import monitor

class DiscoveryTests(unittest.TestCase):
    def test_shelf_json_field_order_and_escaped_titles(self):
        from crawlers.weread import WereadCrawler
        book={'title':'公众号"新名称','readerLink':'https://weread.qq.com/web/mp/reader/abc',
              'bookId':'MP_WXS_1','cover':'https://qpic.cn/a','updateTime':123,
              'lastChapterCreateTime':456,'extra':{'a':1}}
        page='<script>window.__INITIAL_STATE__ = '+json.dumps({'shelf':{'booksAndArchives':[book]}})+';</script>'
        result=WereadCrawler._parse_shelf(page)
        self.assertEqual(result['MP_WXS_1']['nickname'],book['title'])
        self.assertEqual(result['MP_WXS_1']['update_ts'],456)

    def test_profile_links_and_share_text(self):
        cases=[('weibo','https://weibo.com/123','123'),('weibo','来自分享 https://m.weibo.cn/profile/123?x=1','123'),
               ('taoguba','https://m.tgb.cn/user/blog?userID=123','123'),
               ('taoguba','https://www.taoguba.com.cn/blog/123','123'),
               ('zsxq','https://wx.zsxq.com/dweb2/index/group/123','123'),
               ('weread','https://weread.qq.com/web/bookDetail?bookId=MP_WXS_123','MP_WXS_123')]
        for p,q,expected in cases:self.assertEqual(normalize_id(p,q),expected)
        for q in ('https://weibo.com.evil.test/u/123','https://evil.test/weibo.com/u/123','https://weibo.com/status/123'):
            with self.assertRaises(ValueError):normalize_id('weibo',q)

    def test_auto_platform_detection_and_preview(self):
        with patch('discovery.TaogubaCrawler.fetch_profile',return_value={'nickname':'博主','avatar':''}):
            result=discover('weibo','https://www.tgb.cn/blog/123')
        self.assertEqual(result['platform'],'taoguba')
        self.assertEqual(result['candidates'][0]['user_id'],'123')
        self.assertEqual(User(platform='taoguba',user_id='123',display_name='我的名称').display_name,'我的名称')

    def test_weibo_search_nested_cards_dedup(self):
        data={'ok':1,'data':{'cards':[{'card_group':[{'user':{'id':1,'screen_name':'甲'}}]},
                                     {'user':{'id':1,'screen_name':'甲'}}, {'user':{'id':2,'screen_name':'乙'}}]}}
        with patch('httpx.Client.get',return_value=test_core.response(data)):
            result=search_weibo(WeiboCrawler('SUB=test'),'昵称')
        self.assertEqual([p['user_id'] for p in result],['1','2'])

    def test_group_and_shelf_picker(self):
        update(zsxq_token='test',weread_cookie='test')
        with patch('discovery.ZsxqCrawler._get',return_value={'groups':[{'group_id':1,'name':'星球甲'},{'group_id':2,'name':'星球乙'}]}):
            result=discover('zsxq','甲')
        self.assertEqual(len(result['candidates']),1)
        with patch('discovery.WereadCrawler.list_shelf_accounts',return_value=[{'user_id':'MP_WXS_1','nickname':'公众号甲','avatar':''}]):
            self.assertEqual(discover('weread','')['candidates'][0]['platform'],'weread')

    def test_api_schedule_and_discovery_errors(self):
        with TestClient(main.app,base_url='http://127.0.0.1:8000') as c:
            state=c.get('/api/status').json()['scheduler']
            self.assertIn('server_time',state)
            with patch('discovery.discover',return_value={'platform':'weibo','candidates':[]}):
                self.assertEqual(c.post('/api/users/discover',json={'platform':'weibo','query':'甲'}).status_code,200)
            self.assertEqual(c.post('/api/users/discover',json={'platform':'unsupported','query':''}).status_code,404)

if __name__=='__main__':unittest.main()
