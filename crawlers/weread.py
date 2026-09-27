"""公众号监控·微信读书书架桥（v11.3 实测定稿）。

2026-07-30 用真实账号实测结论：
- 续期：POST /web/login/renewal，json 必须是 {"ql": False}（True 会鉴权失败
  并清空会话！），成功后 Set-Cookie 发新 wr_skey/wr_rt，必须写回 config
- Cookie 必须用"罐"（httpx.Cookies）逐条 set，不能用 Cookie 字符串头，
  否则续期后新旧 wr_skey 并存，接口照样 -2012
- /web/shelf/sync 接口可用，但返回里没有公众号（公众号只出现在书架
  网页 /web/shelf 的 booksAndArchives 里）→ 枚举一律解析书架网页
- 公众号文章列表没有任何网页端接口（wewe-rss 是靠作者自建服务器转发
  实现的）→ 文章监测用书架页每本书的 lastChapterCreateTime 变化来发现
  更新，推送"有新文章"提醒，链接指向微信读书该书页

凭证：weread.qq.com 网页版 F12 → 网络 → 任意请求 → 复制整条 Cookie。
"""
import json
import re
import time
from datetime import datetime

import httpx
from lxml import html as lh

from .base import BaseCrawler, AuthExpiredError, RateLimitedError

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
BASE = "https://weread.qq.com"


def extract_book_id(text: str) -> str:
    m = re.search(r"(MP_WXS_\w+)", (text or "").strip())
    return m.group(1) if m else (text or "").strip()


def _vid_from_cookie(cookie: str) -> str:
    m = re.search(r"(?:^|;\s*)wr_vid=(\d+)", cookie or "")
    return m.group(1) if m else ""


def _unesc(s: str) -> str:
    return (s or "").replace("\\u002F", "/").replace("http://", "https://")


class WereadCrawler(BaseCrawler):
    platform = "weread"
    display_name = "公众号"
    needs_cookie = True   # 凭证是微信读书网页版 Cookie

    def _client(self) -> httpx.Client:
        """cookie 逐条入罐（不要 Cookie 字符串头），续期换凭证才干净。"""
        c = httpx.Client(headers={"User-Agent": UA, "Referer": BASE + "/"},
                         timeout=15, follow_redirects=True)
        for part in (self.credential or "").split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                c.cookies.set(k.strip(), v.strip(), domain=".weread.qq.com")
        return c

    def _renew_and_persist(self, client: httpx.Client):
        """续期并把新凭证写回 config。实测 {"ql": False} 才正确。"""
        try:
            r = client.post(f"{BASE}/web/login/renewal", json={"ql": False})
            if '"succ"' not in r.text:
                return
        except Exception:
            return
        new = "; ".join(f"{k}={v}" for k, v in client.cookies.items())
        if new and new != self.credential:
            try:
                from config import load_config, save_config
                cfg = load_config()
                cfg["weread_cookie"] = new
                save_config(cfg)
                self.credential = new
            except Exception:
                pass

    @staticmethod
    def _shelf_page(client: httpx.Client) -> str:
        r = client.get(f"{BASE}/web/shelf")
        if r.status_code != 200:
            raise AuthExpiredError("书架页打不开，请重新粘贴微信读书 Cookie")
        return r.text

    @staticmethod
    def _parse_shelf(page: str) -> dict:
        """从书架页 booksAndArchives 里抠公众号：ID -> {昵称, 头像, 链接, 更新戳}"""
        out = {}
        pat = re.compile(
            r'\{"bookId":"(MP_WXS_\w+)","deepLink":"([^"]*)","title":"([^"]*)"'
            r'.*?"cover":"([^"]*)".*?"updateTime":(\d+),"lastChapterCreateTime":(\d+)',
            re.S)
        for m in pat.finditer(page):
            bid, link, title, cover, upd, last = m.groups()
            out[bid] = {
                "nickname": title,
                "avatar": _unesc(cover),
                "link": _unesc(link),
                "update_ts": int(last or upd or 0),
            }
        return out

    # ---------- 设置页「同步书架」 ----------
    def list_shelf_accounts(self) -> list[dict]:
        if not _vid_from_cookie(self.credential):
            raise AuthExpiredError("Cookie 里没找到 wr_vid，请检查是否复制完整")
        with self._client() as c:
            self._renew_and_persist(c)
            books = self._parse_shelf(self._shelf_page(c))
        return [{"user_id": bid,
                 "nickname": v["nickname"],
                 "avatar": v["avatar"]} for bid, v in books.items()]

    # ---- 手动添加时补全昵称头像（接口已验证可用） ----
    def fetch_profile(self, user_id: str) -> dict:
        bid = extract_book_id(user_id)
        if not bid.startswith("MP_WXS_"):
            raise RuntimeError("请用「同步书架」添加，或填 MP_WXS_ 开头的 ID")
        with self._client() as c:
            self._renew_and_persist(c)
            d = c.get(f"{BASE}/web/book/info", params={"bookId": bid}).json()
            err = d.get("errCode", 0) if isinstance(d, dict) else 1
            if err in (-2010, -2012):
                raise AuthExpiredError("微信读书登录已失效，请重新粘贴 Cookie")
            return {
                "nickname": d.get("title", ""),
                "avatar": _unesc(d.get("cover", "")),
            }

    # ---- 最新文章：mp/cover 拿标题+reviewId，mp/content 拿全文 ----
    # （2026-07-30 实测打通；/web/mp/articles 文章列表接口始终 -2041 不可用，
    #   但监测场景"追最新一篇"足够：reviewId 一变就是新文章）
    def fetch_posts(self, user_id: str) -> list[dict]:
        bid = extract_book_id(user_id)
        with self._client() as c:
            self._renew_and_persist(c)
            d = c.get(f"{BASE}/web/mp/cover", params={"bookId": bid}).json()
            err = d.get("errCode", 0) if isinstance(d, dict) else 1
            if err in (-2010, -2012):
                raise AuthExpiredError("微信读书登录已失效，请在设置页重新粘贴 Cookie")
            if err or not d.get("reviewId"):
                raise RuntimeError(f"获取公众号最新文章失败 errCode={err}")
            rid = d["reviewId"]
            title = d.get("title", "")
            pic = _unesc(d.get("pic", ""))
            author = d.get("name", "")   # 公众号名
            # 「查看原文」用阅读页 /web/mp/reader/{hash}（渲染好的文章页）；
            # hash 从书架页该书的 deepLink（book-detail?type=1&v=hash）里取
            url = f"{BASE}/web/mp/content?reviewId={rid}"   # 兜底（浏览器里会显示成源码）
            try:
                books = self._parse_shelf(self._shelf_page(c))
                link = books.get(bid, {}).get("link", "")
                m = re.search(r"[?&]v=([0-9a-zA-Z]+)", link)
                if m:
                    url = f"{BASE}/web/mp/reader/{m.group(1)}"
            except Exception:
                pass
            # 全文（页面即微信文章 HTML，带 js_content 和 ct 时间戳）
            content_html, images, published = "", [], ""
            try:
                r = c.get(f"{BASE}/web/mp/content", params={"reviewId": rid})
                if r.status_code == 200 and r.text:
                    content_html, images = self._parse_article_html(r.text)
                    m = re.search(r'var ct = "?(\d+)"?', r.text)
                    if m:
                        published = datetime.fromtimestamp(
                            int(m.group(1))).isoformat(timespec="seconds")
            except Exception:
                pass
            if not images and pic:
                images = [pic]
            return [{
                "post_id": rid,   # reviewId 天然唯一，新文章 = 新 ID
                "author_name": author,
                "title": title,
                "content_html": content_html,
                "images": images,
                "url": url,
                "published_at": published,
            }]

    @staticmethod
    def _parse_article_html(text: str) -> tuple[str, list]:
        """从微信文章页 HTML 提取正文和图片（js_content 结构）。"""
        tree = lh.fromstring(text)
        node = tree.xpath("//div[@id='js_content']")
        if not node:
            return "", []
        n = node[0]
        n.attrib.pop("style", None)
        for bad in n.xpath(".//script | .//style | .//iframe"):
            if bad.getparent() is not None:
                bad.getparent().remove(bad)
        images = []
        for img in list(n.iter("img")):
            src = img.get("data-src") or img.get("src") or ""
            if src.startswith("//"):
                src = "https:" + src
            if src.startswith("http"):
                images.append(src.replace("http://", "https://"))
            if img.getparent() is not None:
                img.getparent().remove(img)
        for a in n.iter("a"):
            a.set("target", "_blank")
            a.set("rel", "noopener")
        h = lh.tostring(n, encoding="unicode", method="html")
        h = h[h.find(">") + 1:h.rfind("</div>")]
        return h, images
