"""爬虫基类：三个平台统一接口，新增平台只需继承本类并在 monitor.py 注册。"""


class AuthExpiredError(Exception):
    """凭证过期/失效，界面上会醒目提示用户更新 Cookie/Token。"""


class RateLimitedError(Exception):
    """被平台限流，monitor 会自动退避。"""


class BaseCrawler:
    platform = "base"          # 平台标识
    display_name = "基类"
    needs_cookie = True

    def __init__(self, credential: str = ""):
        self.credential = credential or ""

    def fetch_user_name(self, user_id: str) -> str:
        """根据用户 ID 拉取昵称（可选实现）。"""
        return ""

    def fetch_profile(self, user_id: str) -> dict:
        """拉取用户资料 {"nickname": ..., "avatar": ...}，失败返回空 dict。"""
        try:
            return {"nickname": self.fetch_user_name(user_id), "avatar": ""}
        except Exception:
            return {}

    def fetch_posts(self, user_id: str) -> list[dict]:
        """
        拉取该用户最近的帖子列表，返回标准化的 dict：
        {
            "post_id": "平台内唯一ID",
            "author_name": "昵称",
            "title": "标题（可无）",
            "content_html": "正文 HTML",
            "images": ["https://..."],
            "url": "原文链接",
            "published_at": "2026-07-21T16:26:00",
        }
        凭证失效抛 AuthExpiredError，被限流抛 RateLimitedError。
        """
        raise NotImplementedError
