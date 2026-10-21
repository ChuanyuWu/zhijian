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
        from config import update
        update(users=[dict(platform='weibo',user_id='123',nickname='测试作者',avatar='https://wx1.sinaimg.cn/avatar.jpg',enabled=True),
                      dict(platform='taoguba',user_id='12489969',nickname='作手小五郎',avatar='https://image.tgb.cn/avatar.jpg',enabled=True)])
        post = dict(platform='weibo', author_id='123', author_name='旧昵称', post_id='5352161488930068',
                    post_uid='weibo:123:5352161488930068', title='', content_html='摘要 <img src="https://face.t.sinajs.cn/expression/test.png" alt="[偷笑]"><img src="https://wx1.sinaimg.cn/broken.jpg"><a href="https://weibo.com/status/5352161488930068">全文</a>',
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
                import base64
                pixel=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jxSkAAAAASUVORK5CYII=')
                page.route('**/api/img?*',lambda route:route.fulfill(status=404) if any(x in route.request.url for x in ('sinajs.cn','broken.jpg')) else route.fulfill(content_type='image/png',body=pixel))
                connected={k:{'state':'ok' if k in ('weibo','weread') else 'public' if k=='taoguba' else 'pending','message':'状态说明'} for k in ('weibo','taoguba','zsxq','weread')}
                page.route('**/api/connections',lambda route:route.fulfill(content_type='application/json',body=json.dumps(connected)))
                def status_route(route):
                    data=route.fetch().json();data['status']['weibo']['last_run']='12:34:56'
                    data['scheduler'].update(phase='waiting',next_run_at=time.time()+60,server_time=time.time())
                    route.fulfill(content_type='application/json',body=json.dumps(data))
                page.route('**/api/status',status_route)
                page.goto('http://127.0.0.1:18765/')
                page.locator('.card-body').wait_for()
                assert page.locator('.author').inner_text()=='测试作者'
                url = page.url
                def fail(route):route.fulfill(status=502, content_type='application/json', body=json.dumps({'detail':'全文暂时不可用'}))
                page.route('**/api/posts/*/fulltext', fail)
                assert page.locator('.card-body a').count()==0
                assert page.locator('.card .expand-btn').count()==1
                page.locator('.emoji-fallback').wait_for()
                page.locator('.image-fallback').wait_for()
                page.locator('.card .expand-btn').click()
                page.get_by_text('全文暂时不可用', exact=False).wait_for()
                assert page.url == url, 'fulltext must remain inside the app'
                page.unroute('**/api/posts/*/fulltext', fail)
                completed = dict(database.get_post(post['id']), content_html='这是已获取的完整微博正文。', needs_fulltext=False)
                page.route('**/api/posts/*/fulltext', lambda route: route.fulfill(content_type='application/json',body=json.dumps({'post':completed})))
                page.get_by_role('button',name='展开全文 ▾',exact=True).click()
                page.get_by_text('这是已获取的完整微博正文。',exact=True).wait_for()
                assert page.locator('#statusBar').inner_text().find('12:34:56')>=0
                page.locator('#pollCountdown').wait_for()
                assert '下轮' in page.locator('#pollCountdown').inner_text()
                page.get_by_role('button',name='⚙ 设置').click()
                page.locator('.user-row').nth(1).wait_for()
                assert page.locator('.user-row .avatar').count()==2
                assert page.locator('.user-row').nth(1).inner_text().find('作手小五郎')>=0
                page.get_by_role('button',name='平台账号',exact=True).click()
                page.locator('.connection-card').nth(3).wait_for()
                assert page.locator('.connection-card').count()==4
                page.locator('.connection-card').first.locator('summary').click()
                page.wait_for_timeout(2200)
                assert page.locator('.connection-card').first.locator('details').get_attribute('open') is not None
                page.locator('.connection-card').first.locator('summary').click()
                page.locator('details.manual-credentials summary').click()
                assert page.locator('#weiboCookie').input_value()==''
                page.get_by_role('button',name='监控对象',exact=True).click()
                page.get_by_role('button',name='＋ 添加',exact=True).click()
                candidate=dict(platform='weibo',user_id='999',nickname='新博主',avatar='https://wx1.sinaimg.cn/avatar.jpg',description='请核对身份')
                page.route('**/api/users/discover',lambda route:route.fulfill(content_type='application/json',body=json.dumps({'platform':'weibo','candidates':[candidate]})))
                page.locator('#newUid').fill('新博主')
                page.get_by_role('button',name='查找',exact=True).click()
                page.locator('[data-candidate]').click()
                assert page.locator('.user-row').count()==3
                page.locator('[data-toggle-user="0"]').click()
                assert '已暂停' in page.locator('.user-row').first.inner_text()
                page.get_by_role('button',name='偏好设置',exact=True).click()
                page.locator('#interval').fill('60')
                page.get_by_role('button',name='平台账号',exact=True).click()
                page.get_by_role('button',name='监控对象',exact=True).click()
                assert '已暂停' in page.locator('.user-row').first.inner_text()
                page.get_by_role('button',name='保存更改',exact=True).click()
                page.locator('#drawer.open').wait_for(state='detached')
                with httpx.Client(trust_env=False) as c:
                    cfg=c.get('http://127.0.0.1:18765/api/settings').json()
                    assert cfg['poll_interval_sec']==60 and len(cfg['users'])==3 and not cfg['users'][0]['enabled']
                page.locator('#searchBox').fill('没有匹配结果')
                page.locator('#empty').wait_for(state='visible')
                assert page.locator('.card').count()==0
                page.locator('#searchBox').fill('')
                page.locator('.card').wait_for()
                # New feed event: preserve keyword filtering and deduplicate durable IDs.
                assert page.evaluate("matches({platform:'weibo',author_id:'123',content_html:'正文',title:'',author_name:''})")
                prepared=dict(completed,content_html='完整正文。'*200,needs_fulltext=False)
                page.evaluate('(post)=>applyPreparedPost(post)',prepared)
                assert page.locator('.card .expand-btn').count()==1
                assert page.locator('.collapsible.collapsed').count()==1
                page.unroute('**/api/posts/*/fulltext')
                requests=[]
                page.on('request',lambda request:requests.append(request.url) if request.url.endswith('/fulltext') else None)
                page.locator('.card .expand-btn').click()
                assert page.locator('.collapsible.collapsed').count()==0 and not requests
                page.get_by_role('button',name='⚙ 设置').click()
                page.locator('.user-row').first.wait_for()
                page.wait_for_timeout(350)
                (ROOT / 'test-results').mkdir(exist_ok=True)
                page.screenshot(path=str(ROOT / 'test-results' / 'settings-monitors.png'))
                page.get_by_role('button',name='平台账号',exact=True).click()
                page.locator('.connection-card').nth(3).wait_for()
                page.evaluate("applyTheme('light')")
                page.screenshot(path=str(ROOT / 'test-results' / 'settings-accounts.png'))
                page.set_viewport_size({'width':390,'height':844})
                page.screenshot(path=str(ROOT / 'test-results' / 'settings-mobile.png'))
                assert page.locator('#drawer').evaluate('(el)=>el.scrollWidth<=el.clientWidth')
                assert not errors, errors
                browser.close()
            print('UI PASS: fulltext; avatars; canonical names; polling times; tab draft preservation; connection menu stability; target preview/add; pause/save; search; desktop/mobile; no JS errors')
        finally:
            server.terminate()
            server.wait(timeout=15)

if __name__=='__main__':main()
