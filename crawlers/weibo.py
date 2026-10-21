"""Mobile Weibo list, detail and long text; never rewrite mobile paths to desktop."""
import html
import re
import threading
import time
from datetime import datetime
import httpx
from content_safety import sanitize_html, needs_fulltext
from .base import BaseCrawler, AuthExpiredError, RateLimitedError

UA = ('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) '
      'AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1')
API = 'https://m.weibo.cn/api/container/getIndex'

class WeiboCrawler(BaseCrawler):
    platform = 'weibo'
    display_name = '微博'
    needs_cookie = True
    _cooldown_until = 0
    _cooldown_lock = threading.Lock()

    def _client(self):
        return httpx.Client(headers={'User-Agent': UA, 'Referer': 'https://m.weibo.cn/',
                                     'Cookie': self.credential, 'X-Requested-With': 'XMLHttpRequest'},
                            timeout=15, follow_redirects=True)

    @staticmethod
    def _check_resp(r):
        if r.status_code in (403, 418, 429, 432):
            raise RateLimitedError('微博请求受限，请稍后重试')
        if r.status_code == 401:
            raise AuthExpiredError('微博登录已失效，请重新连接')
        if r.status_code != 200:
            raise RuntimeError('微博接口暂时不可用')
        if 'json' not in r.headers.get('content-type', ''):
            raise AuthExpiredError('微博未返回登录后的数据，请验证连接')
        data = r.json()
        if data.get('ok') == -100:
            raise AuthExpiredError('微博登录已失效，请重新连接')
        if data.get('ok') == 0:
            raise RuntimeError('微博不存在、无权访问，或接口暂时不可用')
        return data

    def _get_container_id(self, client, uid):
        data = self._check_resp(client.get(API, params={'type': 'uid', 'value': uid}))
        d = data.get('data') or {}
        info = d.get('userInfo') or {}
        for tab in (d.get('tabsInfo') or {}).get('tabs', []):
            if tab.get('tab_type') == 'weibo':
                return tab['containerid'], info.get('screen_name', ''), info
        raise RuntimeError('未找到该用户的微博列表')

    def fetch_profile(self, user_id):
        with self._client() as c:
            _, name, info = self._get_container_id(c, user_id)
            return {'nickname': name, 'avatar': info.get('profile_image_url', '')}

    def fetch_user_name(self, user_id):
        return self.fetch_profile(user_id)['nickname']

    @staticmethod
    def _clean_html(raw):
        return sanitize_html(raw, 'https://m.weibo.cn/')

    @staticmethod
    def _is_long(mb):
        return bool(mb.get('isLongText')) or needs_fulltext(mb.get('text', ''))

    @staticmethod
    def _parse_mblog(mb):
        content = WeiboCrawler._clean_html(mb.get('text', ''))
        images = []
        def pictures(node):
            for pic in node.get('pics') or []:
                src = (pic.get('large') or {}).get('url') or pic.get('url')
                if src and src not in images:
                    images.append(src)
        pictures(mb)
        rt = mb.get('retweeted_status') or {}
        if rt:
            name = html.escape((rt.get('user') or {}).get('screen_name', '原博主'))
            content += f'<div class="retweet">🔁 @{name}：{WeiboCrawler._clean_html(rt.get("text", "原微博已不可用"))}</div>'
            pictures(rt)
        try:
            published = datetime.strptime(mb.get('created_at', ''), '%a %b %d %H:%M:%S %z %Y').isoformat(timespec='seconds')
        except (ValueError, TypeError):
            published = mb.get('created_at', '')
        pid = str(mb.get('id', ''))
        if not pid.isdigit():
            raise ValueError('微博缺少 ID')
        return dict(post_id=pid, author_name=(mb.get('user') or {}).get('screen_name', ''),
                    title='', content_html=content, images=images,
                    url=f'https://m.weibo.cn/detail/{pid}', published_at=published,
                    needs_fulltext=WeiboCrawler._is_long(mb) or WeiboCrawler._is_long(rt))

    def _extend(self, client, mb):
        if not self._is_long(mb):
            return
        if time.monotonic() < type(self)._cooldown_until:
            raise RateLimitedError('微博全文请求冷却中，请五分钟后重试')
        try:
            data = self._check_resp(client.get('https://m.weibo.cn/statuses/extend',
                                               params={'id': str(mb['id'])}))
        except RateLimitedError:
            with self._cooldown_lock:
                type(self)._cooldown_until = time.monotonic() + 300
            raise
        text = (data.get('data') or {}).get('longTextContent')
        if not text or needs_fulltext(text):
            raise RuntimeError('暂未取得完整正文，可稍后重试')
        mb['text'], mb['isLongText'] = text, False

    def fetch_article_by_id(self, post_id):
        if not re.fullmatch(r'\d{1,30}', str(post_id)):
            raise ValueError('微博 ID 无效')
        if time.monotonic() < type(self)._cooldown_until:
            raise RateLimitedError('微博全文请求冷却中，请稍后重试')
        with self._client() as c:
            data = self._check_resp(c.get('https://m.weibo.cn/statuses/show', params={'id': post_id}))
            mb = data.get('data') or {}
            if str(mb.get('id', '')) != str(post_id):
                raise RuntimeError('微博不存在或当前账号无权访问')
            self._extend(c, mb)
            rt = mb.get('retweeted_status')
            if rt:
                self._extend(c, rt)
            post = self._parse_mblog(mb)
            if post['needs_fulltext']:
                raise RuntimeError('暂未取得完整正文')
            return post

    def fetch_posts(self, user_id):
        with self._client() as c:
            cid, _, _ = self._get_container_id(c, user_id)
            data = self._check_resp(c.get(API, params={'type': 'uid', 'value': user_id,
                                                      'containerid': cid, 'page': 1}))
            posts = []
            for card in (data.get('data') or {}).get('cards', []):
                if card.get('card_type') == 9 and card.get('mblog'):
                    try:
                        posts.append(self._parse_mblog(card['mblog']))
                    except (ValueError, TypeError):
                        continue
            return posts
