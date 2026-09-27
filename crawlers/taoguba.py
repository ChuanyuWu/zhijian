"""淘股吧爬虫：公开内容免登录（已在 2026-07 实测）。

接口流程：
1. 列表（主用）：GET m.tgb.cn/mBlogTopicAjax?userID={uid}&sortFlag=W&pageNo=1
   -> 移动端 JSON 接口，按最新时间排序，含精确到秒的发帖时间
   【重要】PC 版博客首页 /blog/{uid} 是置顶优先的静态列表，
   置顶多的用户新帖根本不上首页（实测会漏当天新帖），故只作降级兜底
2. 正文：GET www.tgb.cn/a/{短链} -> div.article-text#first 全文 + 图片

注意：
- 大 V 常在同一长帖里持续跟帖更新，本模块监控的是【新主帖】
"""
import re
from datetime import datetime

import httpx
from lxml import html as lh

from .base import BaseCrawler, RateLimitedError

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

BASE = "https://www.tgb.cn"
MOBILE_API = "https://m.tgb.cn/mBlogTopicAjax"

# 每轮最多抓几篇正文（防止新加用户时一口气抓太多触发限流）
MAX_ARTICLE_FETCH_PER_CYCLE = 5


class TaogubaCrawler(BaseCrawler):
    platform = "taoguba"
    display_name = "淘股吧"
    needs_cookie = False

    def _client(self) -> httpx.Client:
        headers = {"User-Agent": UA, "Referer": BASE + "/"}
        if self.credential:
            headers["Cookie"] = self.credential
        return httpx.Client(headers=headers, timeout=15, follow_redirects=True)

    @staticmethod
    def _check(r: httpx.Response):
        if r.status_code in (403, 429):
            raise RateLimitedError(f"淘股吧限流 HTTP {r.status_code}")
        if r.status_code != 200:
            raise RuntimeError(f"淘股吧 HTTP {r.status_code}")
        return r

    def fetch_user_name(self, user_id: str) -> str:
        with self._client() as c:
            r = self._check(c.get(f"{BASE}/user/getBlogerInfo?userID={user_id}"))
            dto = (r.json() or {}).get("dto") or {}
            return dto.get("userName", "")

    def fetch_profile(self, user_id: str) -> dict:
        """getBlogerInfo 接口直接返回昵称和头像路径（pr 字段）。"""
        with self._client() as c:
            r = self._check(c.get(f"{BASE}/user/getBlogerInfo?userID={user_id}"))
            dto = (r.json() or {}).get("dto") or {}
            avatar = ""
            pr = dto.get("pr")
            if pr:
                # _80wh 缩略图免 Referer，适合直接当头像
                avatar = f"https://image.tgb.cn/img/{pr}_80wh.png"
            return {"nickname": dto.get("userName", ""), "avatar": avatar}

    def _fetch_list(self, client: httpx.Client, user_id: str):
        """主用：移动端 JSON 接口，按最新时间排序，不会漏新帖。"""
        r = self._check(client.get(MOBILE_API, params={
            "userID": user_id, "sortFlag": "W", "pageNo": 1,
        }))
        data = r.json()
        if not data.get("status"):
            raise RuntimeError("移动端接口返回 status=false")
        items = []
        for tp in (data.get("dto") or {}).get("listTopic") or []:
            sid = tp.get("newTopicID")
            if not sid:
                continue
            # subject 含 &nbsp; 等 HTML 实体，顺手解码
            title = lh.fromstring(f"<span>{tp.get('subject', '')}</span>").text_content().strip()
            date = tp.get("postDate", "")
            try:
                date = datetime.strptime(date, "%Y-%m-%d %H:%M:%S").isoformat(timespec="seconds")
            except (ValueError, TypeError):
                pass
            items.append({"sid": sid, "title": title, "date": date})
        return items

    def _fetch_list_fallback(self, client: httpx.Client, user_id: str):
        """兜底：PC 版博客首页（置顶优先，可能漏新帖，仅在新接口失效时启用）。"""
        r = self._check(client.get(f"{BASE}/blog/{user_id}"))
        tree = lh.fromstring(r.text)
        items = []
        for div in tree.xpath("//div[@class='article_tittle']"):
            a = div.xpath(".//div[@class='tittle_data left']/a")
            if not a:
                continue
            href = a[0].get("href", "")
            title = a[0].get("title") or "".join(a[0].itertext()).strip()
            date = "".join(div.xpath(".//div[@class='tittle_fbshijian left']/text()")).strip()
            sid = href.rsplit("/", 1)[-1]
            if sid:
                items.append({"sid": sid, "title": title, "date": date})
        return items

    def _get_author(self, client: httpx.Client, user_id: str) -> str:
        try:
            info = self._check(client.get(f"{BASE}/user/getBlogerInfo?userID={user_id}")).json()
            return ((info or {}).get("dto") or {}).get("userName", "") or user_id
        except Exception:
            return user_id

    @staticmethod
    def _fetch_article(client: httpx.Client, sid: str) -> dict:
        r = TaogubaCrawler._check(client.get(f"{BASE}/a/{sid}"))
        tree = lh.fromstring(r.text)

        node = tree.xpath("//div[@class='article-text p_coten']")
        content_html = ""
        images = []
        if node:
            n = node[0]
            # 删除隐藏占位 span [淘股吧]
            for sp in n.xpath(".//span[contains(@style,'display:none')]"):
                sp.getparent().remove(sp)
            # 淘股吧正文图片是懒加载：src 是 placeHolder.png 水印，
            # 真实地址在 data-original（760w）或 src2（原图）。
            # 抽出真实地址交给前端图片网格展示，正文中移除 img 避免重复显示。
            for img in list(n.iter("img")):
                real = img.get("data-original") or img.get("src2") or ""
                if real and "image.tgb.cn" in real:
                    images.append(real)
                parent = img.getparent()
                if parent is not None:
                    parent.remove(img)
            content_html = lh.tostring(n, encoding="unicode", method="html")
            content_html = re.sub(r'^<div[^>]*>|</div>$', "", content_html.strip())

        # 精确时间：正文头部 meta 区域第一个 yyyy-mm-dd hh:mm
        raw = r.text
        mt = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})", raw)
        published = ""
        if mt:
            try:
                published = datetime.strptime(mt.group(1), "%Y-%m-%d %H:%M").isoformat(timespec="seconds")
            except ValueError:
                pass

        tm = re.search(r"<title>(.*?)_", raw)
        title = tm.group(1) if tm else ""
        am = re.search(r"<title>.*?_(.*?)_淘股吧</title>", raw)
        author = am.group(1) if am else ""
        return {
            "post_id": sid,
            "author_name": author,
            "title": title,
            "content_html": content_html,
            "images": images,
            "url": f"{BASE}/a/{sid}",
            "published_at": published,
        }

    def fetch_article_by_id(self, sid: str) -> dict:
        """按短链抓单篇正文（供回填逻辑调用）。"""
        with self._client() as c:
            return self._fetch_article(c, sid)

    def fetch_posts(self, user_id: str) -> list[dict]:
        with self._client() as c:
            try:
                items = self._fetch_list(c, user_id)
            except RateLimitedError:
                raise
            except Exception:
                # 移动接口异常时降级到 PC 博客页
                items = self._fetch_list_fallback(c, user_id)
            author = self._get_author(c, user_id)
            posts = []
            # 只回抓前几篇正文（详情里有精确时间）；列表本身也有标题+日期可作兜底
            for i, it in enumerate(items[:MAX_ARTICLE_FETCH_PER_CYCLE]):
                try:
                    p = self._fetch_article(c, it["sid"])
                    if not p["author_name"]:
                        p["author_name"] = author
                    posts.append(p)
                except RateLimitedError:
                    raise
                except Exception:
                    # 正文抓不到就用列表信息兜底
                    posts.append({
                        "post_id": it["sid"],
                        "author_name": author,
                        "title": it["title"],
                        "content_html": "",
                        "images": [],
                        "url": f"{BASE}/a/{it['sid']}",
                        "published_at": it["date"],
                    })
            # 列表中后面的帖子只留标题级记录，保证时间线完整
            for it in items[MAX_ARTICLE_FETCH_PER_CYCLE:]:
                posts.append({
                    "post_id": it["sid"],
                    "author_name": author,
                    "title": it["title"],
                    "content_html": "",
                    "images": [],
                    "url": f"{BASE}/a/{it['sid']}",
                    "published_at": it["date"],
                })
            return posts
