"""Local-only FastAPI application. Browser login runs in a spawned helper."""
import asyncio
import hashlib
import hmac
import json
import multiprocessing
import os
import secrets
import sys
from contextlib import asynccontextmanager
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from config import BASE_DIR, DATA_DIR, SECRET_KEYS, load_config, mutate_config
from database import count_posts, delete_post, get_post, init_db, query_posts
from settings_schema import SettingsPatch, ResolveRequest, DiscoverRequest, normalize_id
from connections import KEYS
from login_manager import manager
from crawlers.base import AuthExpiredError, RateLimitedError
from fulltext import complete_post
from image_proxy import fetch_image
import monitor

_session_key = secrets.token_bytes(32)
_login_failures = {}

def _token(pw):
    return hmac.new(_session_key, pw.encode(), hashlib.sha256).hexdigest()

@asynccontextmanager
async def lifespan(app):
    init_db()
    task = None
    if os.environ.get('ZHIJIAN_DISABLE_MONITOR') != '1':
        task = asyncio.create_task(monitor.monitor_loop())
    try:
        yield
    finally:
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await manager.close()

app = FastAPI(title='知见', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
STATIC_DIR = os.path.join(BASE_DIR, 'static')

@app.exception_handler(RequestValidationError)
async def invalid_request(request, exc):
    return JSONResponse({'detail': '输入格式不正确，请检查 ID、平台和轮询间隔（30–86400 秒）'}, status_code=422)

@app.middleware('http')
async def local_guard(request: Request, call_next):
    host = (request.url.hostname or '').lower()
    if host not in ('127.0.0.1', 'localhost', '::1'):
        return JSONResponse({'detail': '仅支持本机访问'}, status_code=403)
    if request.url.path.startswith('/api/'):
        origin = request.headers.get('origin')
        if origin:
            parsed = urlparse(origin)
            if parsed.scheme not in ('http', 'https') or parsed.netloc != request.url.netloc:
                return JSONResponse({'detail': '请求来源不匹配'}, status_code=403)
        if request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'detail': '拒绝跨站请求'}, status_code=403)
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            if request.headers.get('content-type', '').split(';')[0] != 'application/json':
                return JSONResponse({'detail': '请使用 JSON 请求'}, status_code=415)
            size = 0
            chunks = []
            async for chunk in request.stream():
                size += len(chunk)
                if size > 1024 * 1024:
                    return JSONResponse({'detail': '请求过大'}, status_code=413)
                chunks.append(chunk)
            request._body = b''.join(chunks)
    try:
        pw = load_config().get('access_password', '')
    except RuntimeError:
        return JSONResponse({'detail': '配置无法读取，请恢复本地备份'}, status_code=503)
    path = request.url.path
    if pw and not (path.startswith('/static/') or path in ('/login', '/api/login')):
        if not hmac.compare_digest(request.cookies.get('pm_token', ''), _token(pw)):
            return JSONResponse({'detail': '请重新登录'}, status_code=401) if path.startswith('/api/') else RedirectResponse('/login')
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    if path.startswith('/api/') and path != '/api/img':
        response.headers['Cache-Control'] = 'no-store'
    return response

@app.exception_handler(Exception)
async def unexpected_error(request, exc):
    # Never echo httpx exception URLs/headers or account credentials.
    return JSONResponse({'detail': '操作失败，请重试或检查本地配置'}, status_code=500)

@app.post('/api/login')
async def api_login(request: Request):
    import time
    client = request.client.host if request.client else 'local'
    failures = [t for t in _login_failures.get(client, []) if t > time.monotonic() - 60]
    _login_failures[client] = failures
    if len(failures) >= 10:
        raise HTTPException(429, '请一分钟后重试')
    body = await request.json()
    pw = load_config().get('access_password', '')
    supplied = body.get('password') if isinstance(body, dict) else None
    if pw and isinstance(supplied, str) and hmac.compare_digest(supplied.encode(), pw.encode()):
        _login_failures.pop(client, None)
        response = JSONResponse({'ok': True})
        response.set_cookie('pm_token', _token(pw), max_age=30*86400, httponly=True, samesite='strict')
        return response
    failures.append(time.monotonic())
    raise HTTPException(401, '密码错误')

@app.get('/login')
async def login_page():
    return FileResponse(os.path.join(STATIC_DIR, 'login.html'))

@app.get('/')
async def index():
    return FileResponse(os.path.join(STATIC_DIR, 'index.html'))

@app.get('/api/posts')
def posts(platform: str = '', author: str = '', q: str = '', offset: int = 0, limit: int = 30):
    if limit < 1 or offset < 0:
        raise HTTPException(422, '分页参数无效')
    return {'posts': query_posts(platform or None, author or None, q or None, offset, min(limit, 100)),
            'total': count_posts()}

@app.delete('/api/posts/{post_id}')
def remove_post(post_id: int):
    if not delete_post(post_id):
        raise HTTPException(404, '帖子不存在')
    return {'ok': True}

@app.post('/api/posts/{post_id}/fulltext')
async def fulltext(post_id: int):
    try:
        return {'post': await asyncio.to_thread(complete_post, post_id)}
    except LookupError:
        raise HTTPException(404, '微博帖子不存在')
    except AuthExpiredError:
        raise HTTPException(401, '微博登录失效，请在设置中重新连接')
    except RateLimitedError:
        raise HTTPException(429, '微博请求受限，请五分钟后重试')
    except Exception:
        raise HTTPException(502, '暂时无法获取全文，帖子可能已删除、无权限或网络异常；摘要已保留')

@app.get('/api/img')
async def proxy_image(url: str = ''):
    return await fetch_image(url)

@app.get('/api/stream')
async def stream():
    q = monitor.subscribe()
    async def generate():
        try:
            while True:
                try:
                    p = await asyncio.wait_for(q.get(), 20)
                    yield f'data: {json.dumps(p, ensure_ascii=False)}\n\n'
                except asyncio.TimeoutError:
                    yield ': keepalive\n\n'
        finally:
            monitor.unsubscribe(q)
    return StreamingResponse(generate(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

@app.get('/api/status')
def status():
    return {'status': monitor.get_status(), 'total': count_posts(),
            'scheduler': monitor.get_schedule()}

@app.get('/api/settings')
def settings():
    cfg = load_config()
    for key in SECRET_KEYS:
        cfg[key] = '●' * 8 if cfg.get(key) else ''
    return cfg

@app.put('/api/settings')
async def save_settings(body: SettingsPatch):
    patch = {k: v for k, v in body.model_dump(exclude_unset=True).items()
             if v is not None and not (k in SECRET_KEYS and v == '●' * 8)}
    # A settings tab may stay open while the browser helper/renewal updates credentials.
    # Untouched secrets are omitted by the UI, rather than copied from a stale snapshot.
    mutate_config(lambda cfg: cfg.update(patch))
    for platform, key in KEYS.items():
        if key in patch:
            manager.results.pop(platform, None)
            job = manager.jobs.get(platform)
            if job and job['public']['state'] not in ('starting', 'waiting'):
                manager.jobs.pop(platform, None)
    response = JSONResponse({'ok': True})
    if patch.get('access_password'):
        response.set_cookie('pm_token', _token(patch['access_password']), httponly=True, samesite='strict')
    monitor.wake()
    return response

def valid_platform(platform):
    if platform not in KEYS:
        raise HTTPException(404, '不支持的平台')
    return platform

@app.post('/api/users/discover')
async def discover_users(body: DiscoverRequest):
    from discovery import discover
    platform = valid_platform(body.platform)
    try:
        return await asyncio.to_thread(discover, platform, body.query)
    except AuthExpiredError:
        raise HTTPException(400, '请先在平台账号页连接该平台')
    except RateLimitedError:
        raise HTTPException(429, '平台暂时限制查询，请稍后再试')
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except Exception:
        raise HTTPException(502, '暂时未能查到对象，请检查平台连接，或改用主页链接')

@app.get('/api/connections')
async def connection_status():
    return {platform: manager.public(platform) for platform in KEYS}

@app.post('/api/connections/{platform}/start')
async def connection_start(platform: str):
    return await manager.start(valid_platform(platform))

@app.get('/api/connections/{platform}')
async def connection_get(platform: str):
    return manager.public(valid_platform(platform))

@app.post('/api/connections/{platform}/cancel')
async def connection_cancel(platform: str):
    return await manager.cancel(valid_platform(platform))

@app.post('/api/connections/{platform}/validate')
async def connection_validate(platform: str):
    return await manager.validate(valid_platform(platform))

@app.delete('/api/connections/{platform}')
async def connection_delete(platform: str):
    result = await manager.disconnect(valid_platform(platform))
    monitor.wake()
    return result

@app.post('/api/users/resolve')
async def resolve(body: ResolveRequest):
    platform = valid_platform(body.platform)
    try:
        uid = normalize_id(platform, body.user_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    credential = load_config().get(KEYS[platform], '')
    if monitor.CRAWLERS[platform].needs_cookie and not credential:
        raise HTTPException(400, '请先连接该平台，再添加监控对象')
    try:
        profile = await asyncio.to_thread(monitor.CRAWLERS[platform](credential).fetch_profile, uid)
        if not profile.get('nickname'):
            raise RuntimeError()
        return dict(profile, user_id=uid)
    except Exception:
        raise HTTPException(502, '未能读取资料，请检查连接和 ID；也可选择仍然添加')

@app.get('/api/weread/shelf')
async def shelf():
    from crawlers.weread import WereadCrawler
    credential = load_config().get('weread_cookie', '')
    if not credential:
        raise HTTPException(400, '请先连接微信读书')
    try:
        return {'accounts': await asyncio.to_thread(WereadCrawler(credential).list_shelf_accounts)}
    except Exception:
        raise HTTPException(502, '书架读取失败，请验证微信读书连接')

app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')

def run():
    if getattr(sys, 'frozen', False):
        os.makedirs(DATA_DIR, exist_ok=True)
        log = open(os.path.join(DATA_DIR, 'run.log'), 'a', encoding='utf-8', buffering=1)
        sys.stdout = sys.stderr = log
    import uvicorn
    import webbrowser
    import threading
    from instance_lock import acquire_instance
    port = int(os.environ.get('ZHIJIAN_PORT', '8000'))
    if not acquire_instance():
        webbrowser.open(f'http://127.0.0.1:{port}')
        return
    if os.environ.get('ZHIJIAN_NO_BROWSER') != '1':
        threading.Timer(1.5, lambda: webbrowser.open(f'http://127.0.0.1:{port}')).start()
    uvicorn.run(app, host='127.0.0.1', port=port, log_level='warning', access_log=False)

if __name__ == '__main__':
    multiprocessing.freeze_support()
    run()
