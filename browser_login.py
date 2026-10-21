"""Spawned browser helper. Secrets travel only over the private process pipe."""
import os
import time
from pathlib import Path

def launch_login_context(chromium, profile_path):
    options = dict(headless=os.environ.get('ZHIJIAN_LOGIN_HEADLESS') == '1',
                   args=['--no-default-browser-check'], accept_downloads=False,
                   timeout=20000)
    try:
        # Keep Edge separate from the existing bundled Chromium profile.
        context = chromium.launch_persistent_context(str(Path(profile_path) / 'edge'),
                                                     channel='msedge', **options)
        return context, 'Edge'
    except Exception:
        # Missing Edge or enterprise launch restrictions must not block login.
        return chromium.launch_persistent_context(profile_path, **options), '内置 Chromium'

def browser_worker(platform, profile_path, pipe, stop):
    from connections import LOGIN_URLS, COOKIE_URLS, credential_from_cookies, validate_credential
    import sys
    root = Path(__file__).resolve().parent
    candidates = [Path(getattr(sys, '_MEIPASS', root)) / 'browsers',
                  root / '_internal' / 'browsers']
    for bundled in candidates:
        if bundled.is_dir():
            os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(bundled)
            break
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            context, browser_name = launch_login_context(p.chromium, profile_path)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(LOGIN_URLS[platform], wait_until='domcontentloaded', timeout=45000)
                pipe.send({'state': 'waiting', 'message': f'已使用{browser_name}打开官方窗口；请用手机对应 App 扫码或完成登录'})
                deadline, last_credential, retry_at = time.monotonic() + 300, '', 0
                while not stop.is_set() and time.monotonic() < deadline:
                    if not context.pages:
                        pipe.send({'state': 'cancelled', 'message': '登录窗口已关闭，原凭据保留'})
                        return
                    credential = credential_from_cookies(platform, context.cookies(COOKIE_URLS[platform]))
                    if credential and (credential != last_credential or time.monotonic() >= retry_at):
                        last_credential, retry_at = credential, time.monotonic() + 20
                        result = validate_credential(platform, credential)
                        if result['state'] in ('ok', 'public'):
                            pipe.send(dict(result, credential=credential))
                            return
                        pipe.send(result | {'state': 'waiting', 'message': result['message'] + '；登录窗口仍可继续操作'})
                    page.wait_for_timeout(1000)
                pipe.send({'state': 'cancelled' if stop.is_set() else 'timeout',
                           'message': '登录已取消或超时，原凭据保留'})
            finally:
                context.close()
    except ModuleNotFoundError:
        pipe.send({'state': 'error', 'message': '源码运行环境缺少浏览器依赖。请关闭程序，双击新版 zhijian_silent.exe；源码开发请先运行 install.bat'})
    except Exception:
        pipe.send({'state': 'error', 'message': '登录窗口无法继续；请检查浏览器组件，或使用手动凭据备用入口'})
    finally:
        pipe.close()
