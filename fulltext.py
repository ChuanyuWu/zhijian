"""Shared full-text jobs for polling and UI, with bounded retry cooldowns."""
import threading
import time
from concurrent.futures import Future
from config import load_config
from database import get_post, save_fulltext
from crawlers.weibo import WeiboCrawler
from crawlers.base import RateLimitedError

_lock = threading.Lock()
_running = {}
_failures = {}

def complete_post(row_id):
    post = get_post(row_id)
    if not post or post['platform'] != 'weibo':
        raise LookupError('微博帖子不存在')
    if post.get('fulltext_state') == 'ready' and not post.get('needs_fulltext'):
        return post
    with _lock:
        now = time.monotonic()
        for key in list(_failures):
            if _failures[key][0] <= now:
                del _failures[key]
        failure = _failures.get(row_id)
        if failure:
            raise failure[1](failure[2])
        future = _running.get(row_id)
        owner = future is None
        if owner:
            future = _running[row_id] = Future()
    if not owner:
        return future.result(timeout=65)
    try:
        credential = load_config().get('weibo_cookie', '')
        if not credential:
            from crawlers.base import AuthExpiredError
            raise AuthExpiredError('请先连接微博账号')
        result = WeiboCrawler(credential).fetch_article_by_id(post['post_id'])
        post = save_fulltext(row_id, result)
        if post is None:
            raise LookupError('帖子已删除')
        future.set_result(post)
        return post
    except Exception as exc:
        with _lock:
            _failures[row_id] = (time.monotonic() + (300 if isinstance(exc, RateLimitedError) else 30),
                                 type(exc), str(exc))
        future.set_exception(exc)
        raise
    finally:
        with _lock:
            _running.pop(row_id, None)


class Prefetcher:
    """Single paced worker; visible posts get a head start before a click."""
    def __init__(self):
        import asyncio
        self.queue = asyncio.Queue(maxsize=100)
        self.queued = set()

    def enqueue(self, posts):
        for post in posts:
            row_id = post.get('id')
            if post.get('platform') != 'weibo' or not post.get('needs_fulltext') or not row_id:
                continue
            with _lock:
                failure = _failures.get(row_id)
                cooling = failure and failure[0] > time.monotonic()
            if cooling or row_id in self.queued or self.queue.full():
                continue
            self.queued.add(row_id)
            self.queue.put_nowait(row_id)

    async def run(self):
        import asyncio
        import monitor
        while True:
            row_id = await self.queue.get()
            delay = 2
            try:
                result = await asyncio.to_thread(complete_post, row_id)
                monitor._broadcast({'event': 'post_updated', 'post': result})
            except RateLimitedError:
                delay = max(2, WeiboCrawler._cooldown_until - time.monotonic())
            except Exception:
                delay = 15  # Slow down after failures; leave the summary readable.
            finally:
                self.queued.discard(row_id)
                self.queue.task_done()
            await asyncio.sleep(delay)
