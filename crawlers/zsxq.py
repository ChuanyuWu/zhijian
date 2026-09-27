"""知识星球爬虫：网页版 API + zsxq_access_token（2026-07 接通）。

接口（均需请求头 Cookie: zsxq_access_token=xxx）：
- GET api.zsxq.com/v2/groups                          星球列表
- GET api.zsxq.com/v2/groups/{gid}                    星球详情（名称、图标）
- GET api.zsxq.com/v2/groups/{gid}/topics?count=30    星球动态时间线
- GET api.zsxq.com/v2/groups/{gid}/users/{uid}/topics 星球内某人的帖子（备用）

监控 ID 格式：
- 整个星球：  "星球ID"            例如 15555418812128
- 星球里某人："星球ID:用户ID"     例如 15555418812128:888555666

⚠️ 合规：只能抓你自己账号有权访问的星球，付费内容不可外传。
"""
import re

import httpx

from .base import BaseCrawler, AuthExpiredError, RateLimitedError

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

API = "https://api.zsxq.com/v2"
WEB = "https://wx.zsxq.com/dweb2/index"


class ZsxqCrawler(BaseCrawler):
    platform = "zsxq"
    display_name = "知识星球"
    needs_cookie = True

    # ---------------- 基础 ----------------
    def _token(self) -> str:
        """宽容处理：用户可能粘贴裸 token，也可能把整条 Cookie 粘进来。"""
        cred = self.credential.strip()
        m = re.search(r"zsxq_access_token=([^;]+)", cred)
        return m.group(1) if m else cred

    def _client(self) -> httpx.Client:
        return httpx.Client(
            headers={
                "User-Agent": UA,
                "Cookie": f"zsxq_access_token={self._token()}",
                "Origin": "https://wx.zsxq.com",
                "Referer": "https://wx.zsxq.com/",
            },
            timeout=15,
            follow_redirects=True,
        )

    def _get(self, client: httpx.Client, path: str, **params):
        # 知识星球服务端偶发"内部错误"，非鉴权类错误重试两次
        import time as _time
        last_err = None
        for attempt in range(3):
            r = client.get(API + path, params=params)
            if r.status_code == 401:
                raise AuthExpiredError("知识星球 Token 已过期，请到设置页更新")
            if r.status_code in (403, 429):
                raise RateLimitedError(f"知识星球限流 HTTP {r.status_code}")
            if r.status_code != 200:
                raise RuntimeError(f"知识星球接口 HTTP {r.status_code}")
            data = r.json()
            if data.get("succeeded", True):
                return data.get("resp_data") or {}
            err = data.get("error") or data.get("info") or "未知错误"
            if "Unauthorized" in str(err) or data.get("code") == 401:
                raise AuthExpiredError("知识星球 Token 已过期，请到设置页更新")
            last_err = err
            _time.sleep(1.5)
        raise RuntimeError(f"知识星球接口错误：{last_err}")

    @staticmethod
    def _split_id(user_id: str) -> tuple[str, str]:
        """'星球ID:用户ID' -> (gid, uid)；纯星球ID -> (gid, '')"""
        parts = str(user_id).strip().split(":")
        gid = re.sub(r"\D", "", parts[0])
        uid = re.sub(r"\D", "", parts[1]) if len(parts) > 1 else ""
        return gid, uid

    # ---------------- 资料 ----------------
    def fetch_profile(self, user_id: str) -> dict:
        gid, uid = self._split_id(user_id)
        with self._client() as c:
            # 星球名 + 星主头像（星球自身常无图标，用星主的）
            nickname, avatar = "", ""
            for g in self._get(c, "/groups").get("groups") or []:
                if str(g.get("group_id")) == gid:
                    nickname = g.get("name", "")
                    avatar = (g.get("owner") or {}).get("avatar_url", "")
                    break
            if not nickname:
                g = self._get(c, f"/groups/{gid}")
                nickname = (g.get("group") or {}).get("name", "")
            # 指定了人：从动态里找这个人的名字和头像
            if uid:
                try:
                    d = self._get(c, f"/groups/{gid}/topics", count=50)
                    for t in d.get("topics") or []:
                        owner = (t.get("talk") or {}).get("owner") or t.get("owner") or {}
                        if str(owner.get("user_id")) == uid:
                            nickname = f"{owner.get('name','')}@{nickname}"
                            avatar = owner.get("avatar_url", "") or avatar
                            break
                except Exception:
                    pass
            return {"nickname": nickname, "avatar": avatar}

    # ---------------- 帖子 ----------------
    @staticmethod
    def _parse_topic(t: dict, group_name: str, filter_uid: str = "") -> dict | None:
        # owner 在 talk/question/answer 内部，顶层可能没有
        talk = t.get("talk") or {}
        owner = talk.get("owner") or t.get("owner") or {}
        if filter_uid and str(owner.get("user_id")) != filter_uid:
            return None

        texts, images = [], []

        def collect(node: dict):
            if not isinstance(node, dict):
                return
            if node.get("text"):
                texts.append(node["text"])
            for img in node.get("images") or []:
                url = ((img.get("original") or {}).get("url")
                       or (img.get("large") or {}).get("url")
                       or (img.get("thumbnail") or {}).get("url"))
                if url:
                    images.append(url)

        ttype = t.get("type", "talk")
        if ttype == "q&a":
            q = t.get("question") or {}
            a = t.get("answer") or {}
            collect(q)
            if q.get("text"):
                texts[0] = "❓ " + texts[0]
            collect(a)
            if a.get("text"):
                texts[-1] = "💡 " + texts[-1]
            # 问答帖用回答者作为作者
            owner = (a.get("owner") or q.get("owner") or owner)
        else:
            collect(talk)
            # 文章帖带链接
            art = talk.get("article") or {}
            if art.get("article_url"):
                texts.append(f"📄 <a href=\"{art['article_url']}\" target=\"_blank\" "
                             f"rel=\"noopener\">{art.get('title','查看文章')}</a>")

        if not texts and not images:
            return None

        content = "<br>".join(
            line.replace("\n", "<br>") for line in texts if line
        )
        return {
            "post_id": str(t.get("topic_id")),
            "author_name": owner.get("name", ""),
            "title": "",
            "content_html": content,
            "images": images,
            "url": f"{WEB}/topic_detail/{t.get('topic_id')}",
            "published_at": t.get("create_time", ""),
        }

    def fetch_posts(self, user_id: str) -> list[dict]:
        gid, uid = self._split_id(user_id)
        if not gid:
            raise RuntimeError("知识星球 ID 格式不对，应为 星球ID 或 星球ID:用户ID")
        with self._client() as c:
            g = self._get(c, f"/groups/{gid}")
            group_name = (g.get("group") or {}).get("name", "")
            d = self._get(c, f"/groups/{gid}/topics", scope="all", count=30)
            posts = []
            for t in d.get("topics") or []:
                try:
                    p = self._parse_topic(t, group_name, filter_uid=uid)
                    if p:
                        posts.append(p)
                except Exception:
                    continue
            return posts
