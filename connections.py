"""Platform-specific read-only credential probes; never return secrets in status."""
import re
import httpx
from crawlers.base import AuthExpiredError, RateLimitedError
from crawlers.weibo import WeiboCrawler
from crawlers.zsxq import ZsxqCrawler
from crawlers.weread import WereadCrawler

KEYS = dict(weibo='weibo_cookie', taoguba='taoguba_cookie', zsxq='zsxq_token', weread='weread_cookie')
LOGIN_URLS = dict(weibo='https://m.weibo.cn/', taoguba='https://www.tgb.cn/',
                  zsxq='https://wx.zsxq.com/', weread='https://weread.qq.com/')
COOKIE_URLS = dict(weibo=['https://m.weibo.cn/'], taoguba=['https://www.tgb.cn/'],
                   zsxq=['https://api.zsxq.com/', 'https://wx.zsxq.com/'],
                   weread=['https://weread.qq.com/'])

def credential_from_cookies(platform, cookies):
    values = {}
    for c in cookies:
        values[c['name']] = c['value']
    required = {'weibo': ['SUB'], 'zsxq': ['zsxq_access_token'],
                'weread': ['wr_vid', 'wr_skey'], 'taoguba': []}[platform]
    if any(not values.get(key) for key in required):
        return ''
    if platform == 'zsxq':
        return values['zsxq_access_token']
    return '; '.join(f'{k}={v}' for k, v in values.items())

def validate_credential(platform, credential):
    if platform == 'taoguba':
        return {'state': 'public', 'message': '公开帖子免登录；可选 Cookie 已保留，登录权限未验证'}
    if not credential:
        return {'state': 'unconfigured', 'message': '尚未连接账号'}
    try:
        if platform == 'weibo':
            crawler = WeiboCrawler(credential)
            with crawler._client() as c:
                d = crawler._check_resp(c.get('https://m.weibo.cn/api/config'))
                if not (d.get('data') or {}).get('login'):
                    raise AuthExpiredError()
        elif platform == 'zsxq':
            crawler = ZsxqCrawler(credential)
            with crawler._client() as c:
                crawler._get(c, '/groups')
        elif platform == 'weread':
            crawler = WereadCrawler(credential)
            with crawler._client() as c:
                r = c.get('https://weread.qq.com/web/shelf')
                if r.status_code in (403, 429):
                    raise RateLimitedError()
                r.raise_for_status()
                # A public shell alone is not proof of login. Require an authenticated
                # shelf payload plus the matching server-rendered account identifier.
                vid = re.search(r'(?:^|;\s*)wr_vid=(\d+)', credential)
                if not vid or 'booksAndArchives' not in r.text or not re.search(
                        r'"(?:vid|userVid)"\s*:\s*"?' + re.escape(vid[1]) + r'\b', r.text):
                    raise AuthExpiredError()
        return {'state': 'ok', 'message': '连接验证成功'}
    except AuthExpiredError:
        return {'state': 'auth_error', 'message': '登录未完成或已失效，请重新连接'}
    except RateLimitedError:
        return {'state': 'rate_limited', 'message': '平台暂时限制请求，请稍后验证'}
    except httpx.HTTPError:
        return {'state': 'network_error', 'message': '网络请求失败，请稍后验证'}
    except Exception:
        return {'state': 'error', 'message': '平台返回异常，暂时无法验证连接'}
