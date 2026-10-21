"""Polling with per-user status, bounded backfill, and durable SSE row IDs."""
import asyncio
import time
from config import load_config, mutate_config
from database import insert_post, get_empty_content_posts, update_post_content, get_post
from content_safety import present_post
from crawlers.base import AuthExpiredError, RateLimitedError
from crawlers.weibo import WeiboCrawler
from crawlers.taoguba import TaogubaCrawler
from crawlers.zsxq import ZsxqCrawler
from crawlers.weread import WereadCrawler
from connections import KEYS

CRAWLERS = dict(weibo=WeiboCrawler, taoguba=TaogubaCrawler, zsxq=ZsxqCrawler, weread=WereadCrawler)
CREDENTIAL_KEYS = KEYS
status = {}
_subscribers = set()
_wake = None
schedule = {'phase': 'idle', 'interval_sec': 120, 'next_run_at': None,
            'last_cycle_at': None, 'current_platform': ''}

def get_schedule():
    result = dict(schedule)
    try:
        result['interval_sec'] = max(30, int(load_config().get('poll_interval_sec', 120)))
    except (ValueError, TypeError):
        pass
    result['server_time'] = time.time()
    return result

def subscribe():
    q = asyncio.Queue(maxsize=200)
    _subscribers.add(q)
    return q

def unsubscribe(q):
    _subscribers.discard(q)

def _broadcast(post):
    for q in list(_subscribers):
        try:
            q.put_nowait(post)
        except asyncio.QueueFull:
            pass

def wake():
    if _wake:
        _wake.set()

def get_status():
    cfg = load_config()
    out = {}
    for platform in CRAWLERS:
        users = [u for u in cfg.get('users', []) if u.get('platform') == platform and u.get('enabled', True)]
        states = [dict(status.get(platform + ':' + u['user_id'],
                       {'state': 'pending', 'message': '等待首次抓取', 'last_run': '', 'new_count': 0}),
                       user_id=u['user_id'], nickname=u.get('nickname', '')) for u in users]
        if not states:
            state, message = 'disabled', '没有启用的监控对象'
        elif CRAWLERS[platform].needs_cookie and not cfg.get(KEYS[platform]):
            state, message = 'disabled', '请先连接平台账号'
        elif all(x['state'] == 'ok' for x in states):
            state, message = 'ok', ''
        elif len({x['state'] for x in states}) > 1:
            state, message = 'partial_error', '部分监控对象尚未成功，请查看各对象状态'
        else:
            state, message = states[0]['state'], states[0]['message']
        out[platform] = dict(state=state, message=message, users=states,
                             last_run=max((s.get('last_run', '') for s in states), default=''),
                             new_count=sum(s.get('new_count', 0) for s in states))
    return out

def _backfill_profile(platform, user, crawler):
    if user.get('nickname') and user.get('avatar'):
        return
    try:
        profile = crawler.fetch_profile(user['user_id'])
    except Exception:
        return
    def apply(cfg):
        for u in cfg['users']:
            if u['platform'] == platform and u['user_id'] == user['user_id']:
                for key in ('nickname', 'avatar'):
                    if not u.get(key) and profile.get(key):
                        u[key] = profile[key]
    mutate_config(apply)

def _run_one_user(platform, user, cfg):
    crawler = CRAWLERS[platform](cfg.get(KEYS[platform], ''))
    posts = crawler.fetch_posts(user['user_id'])
    _backfill_profile(platform, user, crawler)
    # Weibo full text is handled separately; this generic repair is for empty articles.
    if platform != 'weibo' and hasattr(crawler, 'fetch_article_by_id'):
        for row in get_empty_content_posts(platform, user['user_id'], limit=3):
            try:
                article = crawler.fetch_article_by_id(row['post_id'])
                if article.get('content_html'):
                    update_post_content(row['post_uid'], article['content_html'], article.get('images', []), article.get('published_at', ''))
            except RateLimitedError:
                break
            except Exception:
                continue
    new, budget = [], 3
    for p in posts:
        record = dict(p, platform=platform, author_id=user['user_id'],
                      post_uid=f"{platform}:{user['user_id']}:{p['post_id']}")
        if platform != 'zsxq' and user.get('display_name'):
            record['author_name'] = user['display_name']
        if insert_post(record):
            if platform == 'weibo' and record.get('needs_fulltext') and budget:
                budget -= 1
                try:
                    from fulltext import complete_post
                    record = complete_post(record['id'])
                except Exception:
                    pass  # Keep the summary; UI offers an explicit retry.
            new.append(present_post(record))
    return new

async def monitor_loop():
    global _wake
    _wake = asyncio.Event()
    cooldowns = {}
    while True:
        _wake.clear()
        try:
            cfg = load_config()
            try:
                interval = min(86400, max(30, int(cfg.get('poll_interval_sec', 120))))
            except (ValueError, TypeError):
                interval = 120
            schedule.update(phase='polling', interval_sec=interval, next_run_at=None,
                            last_cycle_at=time.time(), current_platform='')
            for user in cfg.get('users', []):
                if not isinstance(user, dict) or not user.get('enabled', True):
                    continue
                platform, uid = user.get('platform'), user.get('user_id')
                if platform not in CRAWLERS or not isinstance(uid, str):
                    continue
                key = platform + ':' + uid
                st = status.setdefault(key, dict(state='pending', message='', last_run='', new_count=0))
                if time.monotonic() < cooldowns.get(platform, 0):
                    st.update(state='rate_limited', message='平台冷却中')
                    continue
                if CRAWLERS[platform].needs_cookie and not cfg.get(KEYS[platform]):
                    st.update(state='disabled', message='尚未连接账号')
                    continue
                try:
                    schedule['current_platform'] = platform
                    # Re-read before each user so refreshed credentials take effect immediately.
                    posts = await asyncio.to_thread(_run_one_user, platform, user, load_config())
                    st.update(state='ok', message='', new_count=st['new_count'] + len(posts))
                    for p in reversed(posts):
                        _broadcast(p)
                except AuthExpiredError:
                    st.update(state='auth_error', message='登录失效，请重新连接')
                except RateLimitedError:
                    st.update(state='rate_limited', message='平台限制请求，五分钟后重试')
                    cooldowns[platform] = time.monotonic() + 300
                except Exception:
                    st.update(state='error', message='抓取失败，请检查网络、ID 和平台连接')
                st['last_run'] = time.strftime('%H:%M:%S')
                if _wake.is_set():
                    break
                await asyncio.sleep(3)
        except Exception:
            interval = 30  # A malformed legacy config must not permanently kill polling.
        schedule.update(phase='waiting', current_platform='', next_run_at=time.time() + interval)
        try:
            await asyncio.wait_for(_wake.wait(), interval)
        except asyncio.TimeoutError:
            pass
