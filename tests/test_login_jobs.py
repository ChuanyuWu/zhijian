"""Helper job lifecycle without real account login or a visible browser."""
import asyncio
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_core  # Shared isolated test DATA_DIR, set before application imports.
import config
from login_manager import LoginManager
from browser_login import launch_login_context
from unittest.mock import Mock

class FakePipe:
    def __init__(self):self.messages=[]
    def poll(self):return bool(self.messages)
    def recv(self):return self.messages.pop(0)
    def close(self):pass

class FakeEvent:
    def __init__(self):self.value=False
    def set(self):self.value=True
    def is_set(self):return self.value

class FakeProcess:
    def __init__(self,*args,**kwargs):self.alive=True;self.started=0
    def start(self):self.started+=1
    def is_alive(self):return self.alive
    def join(self,*args):self.alive=False
    def terminate(self):self.alive=False

class FakeContext:
    def __init__(self):self.parent=FakePipe();self.stop=FakeEvent();self.process=FakeProcess()
    def Pipe(self,**kwargs):return self.parent,FakePipe()
    def Event(self):return self.stop
    def Process(self,**kwargs):return self.process

class LoginJobs(unittest.TestCase):
    def setUp(self):config.save_config(copy.deepcopy(config.DEFAULT_CONFIG))

    def test_edge_preferred_and_bundled_fallback(self):
        browser=Mock()
        context,name=launch_login_context(browser,'isolated-profile')
        self.assertEqual(name,'Edge')
        self.assertEqual(browser.launch_persistent_context.call_args.kwargs['channel'],'msedge')
        self.assertEqual(browser.launch_persistent_context.call_args.args[0],str(Path('isolated-profile')/'edge'))
        browser=Mock()
        browser.launch_persistent_context.side_effect=[RuntimeError('unavailable'),object()]
        context,name=launch_login_context(browser,'isolated-profile')
        self.assertEqual(name,'内置 Chromium')
        self.assertNotIn('channel',browser.launch_persistent_context.call_args.kwargs)
        self.assertEqual(browser.launch_persistent_context.call_args.args[0],'isolated-profile')

    def test_success_single_task_no_secret_and_newer_credential_wins(self):
        async def run():
            for stale in (False,True):
                config.update(weibo_cookie='old')
                manager=LoginManager();ctx=FakeContext()
                with patch('login_manager.multiprocessing.get_context',return_value=ctx):
                    await manager.start('weibo');await manager.start('weibo')
                    self.assertEqual(ctx.process.started,1)
                    if stale:config.update(weibo_cookie='manual')
                    ctx.parent.messages.append({'state':'ok','message':'验证成功','credential':'new'})
                    ctx.process.alive=False
                    await manager.jobs['weibo']['task']
                    self.assertNotIn('credential',manager.public('weibo'))
                    self.assertEqual(config.load_config()['weibo_cookie'],'manual' if stale else 'new')
                    await manager.close()
        asyncio.run(run())

    def test_cancel_ignores_late_success_and_disconnect_is_scoped(self):
        async def run():
            config.update(weibo_cookie='old',weread_cookie='keep')
            manager=LoginManager();ctx=FakeContext()
            with patch('login_manager.multiprocessing.get_context',return_value=ctx):
                await manager.start('weibo')
                ctx.parent.messages.append({'state':'ok','message':'late','credential':'must-not-save'})
                ctx.process.alive=False
                await manager.cancel('weibo')
                self.assertEqual(config.load_config()['weibo_cookie'],'old')
                await manager.disconnect('weibo')
                self.assertEqual(config.load_config()['weibo_cookie'],'')
                self.assertEqual(config.load_config()['weread_cookie'],'keep')
        asyncio.run(run())

if __name__=='__main__':unittest.main()
