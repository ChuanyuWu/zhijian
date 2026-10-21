"""Browser acceptance tests using synthetic posts and an isolated local server."""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def main():
    with tempfile.TemporaryDirectory(prefix='zhijian-tests-ui-', dir=ROOT) as temp:
        os.environ['ZHIJIAN_DATA_DIR'] = temp
        os.environ['ZHIJIAN_DISABLE_MONITOR'] = '1'
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(ROOT / 'browsers')
        import database
        database.init_db()
        post = dict(platform='weibo', author_id='123', author_name='测试作者', post_id='5352161488930068',
                    post_uid='weibo:123:5352161488930068', title='', content_html='摘要 <a href="https://weibo.com/status/5352161488930068">全文</a>',
                    images=[], url='https://m.weibo.cn/detail/5352161488930068', published_at='2026-10-09T17:30:00+08:00', needs_fulltext=True)
        database.insert_post(post)
        server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', '18765', '--no-access-log'], cwd=ROOT,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            import httpx
            with httpx.Client(trust_env=False) as c:
                for _ in range(60):
                    try:
                        if c.get('http://127.0.0.1:18765/api/status').status_code == 200:break
                    except httpx.HTTPError:pass
                    time.sleep(.25)
                else:raise RuntimeError('test server failed to start')
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width':1280, 'height':900})
                errors=[]
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto('http://127.0.0.1:18765/')
                page.locator('.card-body').wait_for()
                url = page.url
                def fail(route):route.fulfill(status=502, content_type='application/json', body=json.dumps({'detail':'全文暂时不可用'}))
                page.route('**/api/posts/*/fulltext', fail)
                page.locator('.card-body a').click()
                page.get_by_text('全文暂时不可用', exact=False).wait_for()
                assert page.url == url, 'fulltext must remain inside the app'
                page.unroute('**/api/posts/*/fulltext', fail)
                completed = dict(database.get_post(post['id']), content_html='这是已获取的完整微博正文。', needs_fulltext=False)
                page.route('**/api/posts/*/fulltext', lambda route: route.fulfill(content_type='application/json',body=json.dumps({'post':completed})))
                page.get_by_role('button',name='获取全文',exact=True).click()
                page.get_by_text('这是已获取的完整微博正文。',exact=True).wait_for()
                page.get_by_role('button',name='⚙ 设置').click()
                page.get_by_text('① 连接平台账号',exact=True).wait_for()
                page.locator('.connection-card').nth(3).wait_for()
                assert page.locator('.connection-card').count()==4
                page.locator('details.manual-credentials summary').click()
                assert page.locator('#weiboCookie').input_value()==''
                page.locator('#interval').fill('60')
                page.get_by_role('button',name='保存',exact=True).click()
                page.locator('#drawer.open').wait_for(state='detached')
                page.locator('#searchBox').fill('没有匹配结果')
                page.locator('#empty').wait_for(state='visible')
                assert page.locator('.card').count()==0
                page.locator('#searchBox').fill('')
                page.locator('.card').wait_for()
                # New feed event: preserve keyword filtering and deduplicate durable IDs.
                assert page.evaluate("matches({platform:'weibo',author_id:'123',content_html:'正文',title:'',author_name:''})")
                page.get_by_role('button',name='⚙ 设置').click()
                page.locator('.connection-card').first.wait_for()
                page.wait_for_timeout(350)
                (ROOT / 'test-results').mkdir(exist_ok=True)
                page.screenshot(path=str(ROOT / 'test-results' / 'settings.png'))
                assert not errors, errors
                browser.close()
            print('UI PASS: old fulltext intercepted; failure retained; internal expansion; four connections; settings save; search; no JS errors')
        finally:
            server.terminate()
            server.wait(timeout=15)

if __name__=='__main__':main()
