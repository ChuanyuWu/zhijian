"""Spawned browser helper. Secrets travel only over the private process pipe."""
import os
import time
from pathlib import Path

def browser_worker(platform, profile_path, pipe, stop):
    from connections import LOGIN_URLS, COOKIE_URLS, credential_from_cookies, validate_credential
    import sys
    bundled = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'browsers'
    if bundled.is_dir():
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(bundled)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(profile_path,
                         headless=os.environ.get('ZHIJIAN_LOGIN_HEADLESS') == '1',
                         args=['--no-default-browser-check'], accept_downloads=False)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(LOGIN_URLS[platform], wait_until='domcontentloaded', timeout=45000)
                pipe.send({'state': 'waiting', 'message': '请在官方窗口完成登录；扫码需使用手机对应 App'})
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
    except Exception:
        pipe.send({'state': 'error', 'message': '登录窗口无法继续；请检查浏览器组件，或使用手动凭据备用入口'})
    finally:
        pipe.close()
