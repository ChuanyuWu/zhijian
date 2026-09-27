"""微博爬虫：m.weibo.cn 移动端 API + Cookie。

接口流程：
1. GET /api/container/getIndex?type=uid&value={uid}  -> 找到 "微博" tab 的 containerid
2. GET /api/container/getIndex?type=uid&value={uid}&containerid={cid}&page=1
   -> data.cards[] 中 card_type==9 的 mblog 即帖子

注意：
- 必须带含 SUB= 的登录 Cookie，否则返回 ok=-100 / Sina Visitor System
- 风控对频率敏感，monitor 已做间隔控制；仍建议用小号 Cookie
"""
import re
from datetime import datetime

import httpx
from lxml import html as lh

from .base import BaseCrawler, AuthExpiredError, RateLimitedError

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

API = "https://m.weibo.cn/api/container/getIndex"


class WeiboCrawler(BaseCrawler):
    platform = "weibo"
    display_name = "微博"
    needs_cookie = True

    def _client(self) -> httpx.Client:
        return httpx.Client(
            headers={
                "User-Agent": UA,
                "Referer": "https://m.weibo.cn/",
                "Cookie": self.credential,
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=15,
            follow_redirects=True,
        )

    @staticmethod
    def _check_resp(r: httpx.Response):
        if r.status_code == 432:
            raise RateLimitedError("微博返回 432，被限流，稍后自动重试")
        if r.status_code != 200:
            raise RuntimeError(f"微博接口 HTTP {r.status_code}")
        ct = r.headers.get("content-type", "")
        if "json" not in ct:
            # 被导到 Sina Visitor System 登录页 = Cookie 失效
            raise AuthExpiredError("微博 Cookie 失效，请更新")
        data = r.json()
        if data.get("ok") == -100:
            raise AuthExpiredError("微博 Cookie 失效（ok=-100），请更新")
        return data

    def _get_container_id(self, client: httpx.Client, uid: str) -> tuple[str, str, dict]:
        data = self._check_resp(client.get(API, params={"type": "uid", "value": uid}))
        d = data.get("data") or {}
        info = d.get("userInfo") or {}
        name = info.get("screen_name", "")
        for tab in (d.get("tabsInfo") or {}).get("tabs", []):
            if tab.get("tab_type") == "weibo":
                return tab["containerid"], name, info
        raise RuntimeError("未找到微博 tab（该用户可能没有公开微博）")

    def fetch_user_name(self, user_id: str) -> str:
        with self._client() as c:
            _, name, _ = self._get_container_id(c, user_id)
            return name

    def fetch_profile(self, user_id: str) -> dict:
        with self._client() as c:
            _, name, info = self._get_container_id(c, user_id)
            return {"nickname": name, "avatar": info.get("profile_image_url", "")}

    @staticmethod
    def _clean_html(raw: str) -> str:
        """清洗微博正文 HTML：
        - 相对链接（/status/xxx 等）改写为 weibo.com 绝对地址
        - 所有链接强制新标签页打开（否则会跳到我们自己域名上 404）
        - 移除脚本/样式
        """
        tree = lh.fromstring(f"<div>{raw}</div>")
        for bad in tree.xpath(".//script | .//style"):
            bad.getparent().remove(bad)
        for a in tree.iter("a"):
            href = a.get("href", "")
            if href.startswith("//"):
                a.set("href", "https:" + href)
            elif href.startswith("/"):
                a.set("href", "https://weibo.com" + href)
            a.set("target", "_blank")
            a.set("rel", "noopener")
        content = lh.tostring(tree, encoding="unicode", method="html")
        return content[5:-6]  # 去掉外包 div

    @staticmethod
    def _parse_mblog(mb: dict) -> dict:
        bid = mb.get("bid") or str(mb.get("id"))
        content = WeiboCrawler._clean_html(mb.get("text", ""))

        images = []
        for pic in mb.get("pics") or []:
            large = (pic.get("large") or {}).get("url") or pic.get("url")
            if large:
                images.append(large)

        # 转发微博：拼接原文
        rt = mb.get("retweeted_status")
        if rt and rt.get("text"):
            rt_user = (rt.get("user") or {}).get("screen_name", "原博主")
            rt_text = re.sub(r"<[^>]+>", "", rt["text"])
            content += f'<div class="retweet">🔁 @{rt_user}：{rt_text}</div>'
            for pic in rt.get("pics") or []:
                large = (pic.get("large") or {}).get("url") or pic.get("url")
                if large:
                    images.append(large)

        # 时间："Tue Jul 21 08:26:00 +0800 2026"
        ts = mb.get("created_at", "")
        try:
            dt = datetime.strptime(ts, "%a %b %d %H:%M:%S %z %Y")
            published = dt.isoformat(timespec="seconds")
        except (ValueError, TypeError):
            published = ts

        return {
            "post_id": str(mb.get("id")),
            "author_name": (mb.get("user") or {}).get("screen_name", ""),
            "title": "",
            "content_html": content,
            "images": images,
            "url": f"https://m.weibo.cn/detail/{bid}",
            "published_at": published,
        }

    def fetch_posts(self, user_id: str) -> list[dict]:
        with self._client() as c:
            cid, _, _ = self._get_container_id(c, user_id)
            data = self._check_resp(c.get(API, params={
                "type": "uid", "value": user_id, "containerid": cid, "page": 1,
            }))
            posts = []
            for card in (data.get("data") or {}).get("cards", []):
                if card.get("card_type") == 9 and card.get("mblog"):
                    try:
                        posts.append(self._parse_mblog(card["mblog"]))
                    except Exception:
                        continue
            return posts
