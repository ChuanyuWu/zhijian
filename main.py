"""FastAPI 入口：API + 静态页面 + SSE 实时推送 + 可选访问密码。"""
import asyncio
import hashlib
import json
import os
import secrets
import sys
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import (HTMLResponse, JSONResponse, RedirectResponse,
                               StreamingResponse, FileResponse)
from fastapi.staticfiles import StaticFiles

from config import load_config, save_config, BASE_DIR
from database import query_posts, count_posts, init_db
import monitor


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from repair import run_startup_repairs
    run_startup_repairs()   # 启动自愈：修复历史数据，失败不影响启动
    asyncio.create_task(monitor.monitor_loop())
    yield


app = FastAPI(title="知见", docs_url=None, redoc_url=None, lifespan=lifespan)

STATIC_DIR = f"{BASE_DIR}/static"


# ---------- 简易访问密码 ----------
def _token(pw: str) -> str:
    return hashlib.sha256(("post-monitor:" + pw).encode()).hexdigest()


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    pw = load_config().get("access_password", "")
    if not pw:
        return await call_next(request)
    path = request.url.path
    if path.startswith("/static") or path in ("/login", "/api/login"):
        return await call_next(request)
    if request.cookies.get("pm_token") == _token(pw):
        return await call_next(request)
    if path.startswith("/api"):
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return RedirectResponse("/login")


@app.post("/api/login")
async def api_login(request: Request):
    body = await request.json()
    pw = load_config().get("access_password", "")
    if pw and body.get("password") == pw:
        resp = JSONResponse({"ok": True})
        resp.set_cookie("pm_token", _token(pw), max_age=30 * 86400, httponly=True)
        return resp
    raise HTTPException(401, "密码错误")


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return FileResponse(f"{STATIC_DIR}/login.html")


# ---------- 页面 ----------
@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(f"{STATIC_DIR}/index.html")


# ---------- 数据 API ----------
@app.get("/api/posts")
async def api_posts(platform: str = "", author: str = "", q: str = "",
                    offset: int = 0, limit: int = 30):
    posts = query_posts(
        platform=platform or None,
        author_id=author or None,
        keyword=q or None,
        offset=max(offset, 0),
        limit=min(limit, 100),
    )
    return {"posts": posts, "total": count_posts()}


@app.delete("/api/posts/{post_id}")
async def api_delete_post(post_id: int):
    from database import delete_post
    if not delete_post(post_id):
        raise HTTPException(404, "帖子不存在")
    return {"ok": True}


# ---------- 图片代理（绕开平台防盗链） ----------
IMG_REFERERS = {
    "tgb.cn": "https://www.tgb.cn/",
    "sinaimg.cn": "https://weibo.com/",
    "sina.com.cn": "https://weibo.com/",
    "zsxq.com": "https://wx.zsxq.com/",
    "qpic.cn": "https://mp.weixin.qq.com/",   # 微信公众号图片
    "qlogo.cn": "https://weread.qq.com/",     # 微信头像/公众号头像
    "weread.qq.com": "https://weread.qq.com/",
    "myqcloud.com": "https://weread.qq.com/",
}
_IMG_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


@app.get("/api/img")
async def proxy_img(url: str = ""):
    from urllib.parse import urlparse
    if not url.startswith("http"):
        raise HTTPException(400, "bad url")
    host = urlparse(url).netloc
    referer = next((v for k, v in IMG_REFERERS.items() if k in host), None)
    headers = {"User-Agent": _IMG_UA}
    if referer:
        headers["Referer"] = referer
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
            r = await client.get(url, headers=headers)
            # 知识星球防盗链：带错 Referer 会返回 ~2KB 水印图。
            # 检测到水印就改用"裸请求"（不带任何 Referer）重试
            if "zsxq.com" in host and len(r.content) < 20000:
                r2 = await client.get(url, headers={"User-Agent": _IMG_UA})
                if r2.status_code == 200 and len(r2.content) > len(r.content):
                    r = r2
        if r.status_code != 200:
            raise HTTPException(502, f"upstream {r.status_code}")
        return StreamingResponse(
            iter([r.content]),
            media_type=r.headers.get("content-type", "image/jpeg"),
            headers={"Cache-Control": "public, max-age=86400"},
        )
    except httpx.HTTPError as e:
        raise HTTPException(502, str(e))


@app.get("/api/stream")
async def api_stream():
    q = monitor.subscribe()

    async def gen():
        try:
            while True:
                post = await q.get()
                yield f"data: {json.dumps(post, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            monitor.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/status")
async def api_status():
    return {"status": monitor.get_status(), "total": count_posts()}


# ---------- 设置 API ----------
@app.get("/api/settings")
async def get_settings():
    cfg = load_config()
    # 凭证打码回显（前端留空表示不修改）
    masked = dict(cfg)
    for k in ("weibo_cookie", "taoguba_cookie", "zsxq_token",
              "weread_cookie", "access_password"):
        v = cfg.get(k, "")
        masked[k] = ("●" * 8) if v else ""
    return masked


@app.put("/api/settings")
async def put_settings(request: Request):
    body = await request.json()
    cfg = load_config()
    for k in ("weibo_cookie", "taoguba_cookie", "zsxq_token",
              "weread_cookie", "access_password", "poll_interval_sec"):
        if k in body:
            v = body[k]
            if isinstance(v, str) and v == "●" * 8:
                continue  # 未修改
            cfg[k] = v
    if "users" in body:
        cfg["users"] = body["users"]
    save_config(cfg)
    return {"ok": True}


@app.post("/api/users/resolve")
async def resolve_user(request: Request):
    """添加用户时自动补全昵称。"""
    body = await request.json()
    platform = body.get("platform", "")
    uid = body.get("user_id", "").strip()
    if platform not in monitor.CRAWLERS or not uid:
        raise HTTPException(400, "参数不完整")
    cls = monitor.CRAWLERS[platform]
    if cls.needs_cookie and not load_config().get(monitor.CREDENTIAL_KEYS[platform], ""):
        raise HTTPException(400, f"请先在下方填写{cls.display_name}的凭证再添加")
    cred = load_config().get(monitor.CREDENTIAL_KEYS[platform], "")
    try:
        profile = cls(cred).fetch_profile(uid)
    except Exception as e:
        raise HTTPException(400, f"获取用户信息失败：{e}")
    out = {"nickname": profile.get("nickname", ""), "avatar": profile.get("avatar", "")}
    if platform == "weread":
        from crawlers.weread import extract_book_id
        out["user_id"] = extract_book_id(uid)   # 用户可能粘的是整段网址
    return out


# ---------- 公众号·微信读书书架同步 ----------
@app.get("/api/weread/shelf")
async def weread_shelf():
    """枚举微信读书书架上订阅的公众号（设置页「同步书架」用）。"""
    cred = load_config().get("weread_cookie", "")
    if not cred:
        raise HTTPException(400, "请先填写微信读书 Cookie 并保存")
    from crawlers.weread import WereadCrawler
    try:
        accounts = await asyncio.to_thread(WereadCrawler(cred).list_shelf_accounts)
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"accounts": accounts}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

if __name__ == "__main__":
    if getattr(sys, "frozen", False):
        # exe 形态（尤其无窗口版）：没有控制台，把所有输出/报错写入日志文件
        from config import DATA_DIR
        os.makedirs(DATA_DIR, exist_ok=True)
        logf = open(os.path.join(DATA_DIR, "run.log"), "a",
                    encoding="utf-8", buffering=1)
        sys.stdout = logf
        sys.stderr = logf
        import logging
        logging.basicConfig(stream=logf, level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(message)s")
        sys.excepthook = lambda t, v, tb: logging.error(
            "uncaught exception", exc_info=(t, v, tb))
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
