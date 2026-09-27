"""轮询调度器：按配置间隔轮询所有启用用户，新帖入库并广播 SSE。"""
import asyncio
import time

from config import load_config
from database import (insert_post, init_db,
                      get_empty_content_posts, update_post_content)
from crawlers.base import AuthExpiredError, RateLimitedError
from crawlers.weibo import WeiboCrawler
from crawlers.taoguba import TaogubaCrawler
from crawlers.zsxq import ZsxqCrawler
from crawlers.weread import WereadCrawler

CRAWLERS = {
    "weibo": WeiboCrawler,
    "taoguba": TaogubaCrawler,
    "zsxq": ZsxqCrawler,
    "weread": WereadCrawler,  # 公众号·微信读书书架桥（MP_WXS_ 开头的 ID）
}

CREDENTIAL_KEYS = {
    "weibo": "weibo_cookie",
    "taoguba": "taoguba_cookie",
    "zsxq": "zsxq_token",
    "weread": "weread_cookie",
}

# 平台状态：ok / auth_error / rate_limited / error / disabled
status = {p: {"state": "ok", "message": "", "last_run": "", "new_count": 0}
          for p in CRAWLERS}

# SSE 订阅者
_subscribers: set[asyncio.Queue] = set()


def subscribe() -> asyncio.Queue:
    q = asyncio.Queue(maxsize=200)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue):
    _subscribers.discard(q)


def _broadcast(post: dict):
    for q in list(_subscribers):
        try:
            q.put_nowait(post)
        except asyncio.QueueFull:
            pass


def get_status():
    return status


def _backfill_profile(platform: str, user: dict, crawler, posts: list):
    """昵称/头像为空时自动补全（例如添加用户时取昵称失败的情况）。"""
    need_name = not user.get("nickname")
    need_avatar = not user.get("avatar")
    if not (need_name or need_avatar):
        return
    nickname = user.get("nickname", "")
    avatar = user.get("avatar", "")
    # 优先走资料接口（知识星球整个星球模式下，名字应是星球名而非发帖人）
    try:
        profile = crawler.fetch_profile(user["user_id"])
        nickname = nickname or profile.get("nickname", "")
        avatar = avatar or profile.get("avatar", "")
    except Exception:
        pass
    # 兜底：用帖子的作者名
    if need_name and not nickname and posts:
        nickname = posts[0].get("author_name", "") or nickname
    if nickname != user.get("nickname") or avatar != user.get("avatar"):
        from config import save_config
        cfg = load_config()
        for u in cfg.get("users", []):
            if u.get("platform") == platform and u.get("user_id") == user["user_id"]:
                u["nickname"] = nickname
                u["avatar"] = avatar
        save_config(cfg)
        user["nickname"], user["avatar"] = nickname, avatar


def _run_one_user(platform: str, user: dict, cfg: dict) -> int:
    """同步执行一轮抓取（在线程池中调用）。返回新增帖子数。"""
    cls = CRAWLERS[platform]
    cred = cfg.get(CREDENTIAL_KEYS.get(platform, ""), "")
    crawler = cls(cred)
    uid = user["user_id"]
    new_posts = []
    all_posts = crawler.fetch_posts(uid)
    _backfill_profile(platform, user, crawler, all_posts)

    # 回填：该用户近期"只有标题没有正文"的帖子，每轮补抓 3 篇
    if hasattr(crawler, "fetch_article_by_id"):
        for row in get_empty_content_posts(platform, uid, limit=3):
            try:
                art = crawler.fetch_article_by_id(row["post_id"])
                if art.get("content_html"):
                    update_post_content(row["post_uid"], art["content_html"],
                                        art.get("images", []), art.get("published_at", ""))
            except Exception:
                continue

    for p in all_posts:
        record = dict(p)
        record["platform"] = platform
        record["author_id"] = uid
        record["post_uid"] = f"{platform}:{uid}:{p['post_id']}"
        if insert_post(record):
            record["row_id"] = None
            new_posts.append(record)
    return new_posts


async def monitor_loop():
    init_db()
    while True:
        cfg = load_config()
        interval = max(30, int(cfg.get("poll_interval_sec", 120)))
        loop = asyncio.get_running_loop()

        for user in cfg.get("users", []):
            if not user.get("enabled", True):
                continue
            platform = user.get("platform")
            if platform not in CRAWLERS:
                continue

            st = status[platform]
            # 需要凭证但未配置（如知识星球未填 Token）→ 标记未启用并跳过
            cls = CRAWLERS[platform]
            if cls.needs_cookie and not cfg.get(CREDENTIAL_KEYS.get(platform, ""), ""):
                st.update(state="disabled",
                          message="未配置凭证，请到设置页填写",
                          last_run=time.strftime("%H:%M:%S"))
                continue
            try:
                new_posts = await loop.run_in_executor(None, _run_one_user, platform, user, cfg)
                st.update(state="ok", message="", last_run=time.strftime("%H:%M:%S"))
                # 倒序广播，保证界面上旧帖先到、新帖后到
                for record in reversed(new_posts):
                    st["new_count"] += 1
                    _broadcast(record)
            except AuthExpiredError as e:
                st.update(state="auth_error", message=str(e), last_run=time.strftime("%H:%M:%S"))
            except RateLimitedError as e:
                st.update(state="rate_limited", message=str(e), last_run=time.strftime("%H:%M:%S"))
            except Exception as e:
                st.update(state="error", message=f"{type(e).__name__}: {e}",
                          last_run=time.strftime("%H:%M:%S"))

            await asyncio.sleep(3)  # 用户之间留间隔，降低风控压力

        await asyncio.sleep(interval)
