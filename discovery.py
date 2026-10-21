"""Find monitoring targets without requiring people to extract numeric IDs."""
from urllib.parse import urlparse, unquote
import re
from connections import KEYS
from config import load_config
from settings_schema import normalize_id
from crawlers.weibo import WeiboCrawler, API
from crawlers.zsxq import ZsxqCrawler
from crawlers.weread import WereadCrawler
from crawlers.taoguba import TaogubaCrawler
from crawlers.base import AuthExpiredError

CRAWLERS = dict(weibo=WeiboCrawler, taoguba=TaogubaCrawler, zsxq=ZsxqCrawler, weread=WereadCrawler)

def detect_platform(text, fallback):
    match = re.search(r'https?://[^\s<>"，]+', text)
    if not match:
        return fallback
    host = (urlparse(match[0]).hostname or '').lower()
    for p, domains in {'weibo': ('weibo.com','weibo.cn'), 'taoguba': ('tgb.cn','taoguba.com.cn'),
                       'zsxq': ('zsxq.com',), 'weread': ('weread.qq.com',)}.items():
        if any(host == d or host.endswith('.'+d) for d in domains):
            return p
    raise ValueError('请粘贴支持平台的主页链接')

def search_weibo(crawler, query):
    query = query.lstrip('@').strip()
    if 'weibo.com/n/' in query:
        query = unquote(urlparse(query).path.split('/n/',1)[1]).strip('/')
    with crawler._client() as c:
        d = crawler._check_resp(c.get(API, params={'containerid':f'100103type=3&q={query}&t=0',
                                                 'page_type':'searchall', 'page':1}))
    results = {}
    def visit(cards):
        for card in cards:
            if not isinstance(card, dict):continue
            user = card.get('user') or {}
            if user.get('id') and user.get('screen_name'):
                uid = str(user['id'])
                if uid.isdigit():
                    results[uid] = dict(platform='weibo',user_id=uid,nickname=user['screen_name'],
                                        avatar=user.get('profile_image_url',''),
                                        description=user.get('description','')[:160])
            visit(card.get('card_group') or [])
    visit((d.get('data') or {}).get('cards') or [])
    return list(results.values())[:20]

def discover(platform, query):
    platform = detect_platform(query, platform)
    if platform not in CRAWLERS:
        raise ValueError('不支持的平台')
    cfg = load_config()
    credential = cfg.get(KEYS[platform], '')
    crawler = CRAWLERS[platform](credential)
    if crawler.needs_cookie and not credential:
        raise AuthExpiredError('请先连接对应平台')
    query = query.strip()
    try:
        uid = normalize_id(platform,query) if query else ''
    except ValueError:
        uid = ''
    if uid:
        profile = crawler.fetch_profile(uid)
        if not profile.get('nickname'):
            raise ValueError('未找到对象，请检查主页链接')
        return {'platform':platform,'candidates':[dict(profile,platform=platform,user_id=uid)]}
    if platform == 'weibo':
        if not query:raise ValueError('请输入微博昵称或主页链接')
        if '://' in query and not re.match(r'^https?://(?:www\.)?weibo\.com/n/',query):
            raise ValueError('请粘贴博主主页链接，不能用帖子链接代替')
        return {'platform':platform,'candidates':search_weibo(crawler,query)}
    if platform == 'weread':
        candidates = [dict(p,platform=platform) for p in crawler.list_shelf_accounts()]
    elif platform == 'zsxq':
        with crawler._client() as c:
            groups = crawler._get(c,'/groups').get('groups') or []
        candidates = [dict(platform=platform,user_id=str(g['group_id']),nickname=g.get('name',''),
                           avatar=(g.get('owner') or {}).get('avatar_url','')) for g in groups if g.get('group_id')]
    else:
        raise ValueError('淘股吧请粘贴博主主页链接，无需寻找或提取 UID')
    if query:
        candidates = [p for p in candidates if query.casefold() in (p['nickname']+' '+p['user_id']).casefold()]
    return {'platform':platform,'candidates':candidates[:200]}
