"""One active, cancellable login job per platform; no secrets in public results."""
import asyncio
import multiprocessing
import shutil
from pathlib import Path
from config import BROWSER_DIR, load_config, replace_credential, update
from connections import KEYS, validate_credential
from browser_login import browser_worker

ACTIVE = {'starting', 'waiting'}

class LoginManager:
    def __init__(self):
        self.jobs = {}
        self.results = {}
        self.locks = {}

    def public(self, platform):
        job = self.jobs.get(platform)
        if job:
            return dict(job['public'])
        if platform in self.results:
            return dict(self.results[platform])
        return {'state': 'public' if platform == 'taoguba' else (
                'pending' if load_config().get(KEYS[platform]) else 'unconfigured'),
                'message': '公开帖子免登录' if platform == 'taoguba' else '请验证连接或重新登录'}

    def lock(self, platform):
        return self.locks.setdefault(platform, asyncio.Lock())

    async def start(self, platform):
        async with self.lock(platform):
            job = self.jobs.get(platform)
            if job and job['public']['state'] in ACTIVE:
                return self.public(platform)
            if job:
                await self._cancel(platform)
            ctx = multiprocessing.get_context('spawn')
            parent, child = ctx.Pipe(duplex=False)
            stop = ctx.Event()
            profile = Path(BROWSER_DIR) / platform
            profile.mkdir(parents=True, exist_ok=True)
            process = ctx.Process(target=browser_worker, args=(platform, str(profile), child, stop), daemon=True)
            job = dict(process=process, pipe=parent, stop=stop,
                       expected=load_config().get(KEYS[platform], ''),
                       public={'state': 'starting', 'message': '正在打开官方登录窗口…'})
            self.jobs[platform] = job
            try:
                process.start()
                child.close()
                job['task'] = asyncio.create_task(self._watch(platform, job))
            except Exception:
                parent.close()
                child.close()
                self.jobs.pop(platform, None)
                self.results[platform] = {'state': 'error', 'message': '无法启动登录窗口'}
            return self.public(platform)

    async def _watch(self, platform, job):
        try:
            while True:
                while job['pipe'].poll():
                    result = job['pipe'].recv()
                    credential = result.pop('credential', None)
                    if job['stop'].is_set():
                        continue
                    if credential:
                        if not replace_credential(KEYS[platform], job['expected'], credential):
                            result = {'state': 'pending', 'message': '凭据已被其他操作更新，请验证当前连接'}
                    job['public'] = result
                if not job['process'].is_alive():
                    if job['public']['state'] in ACTIVE:
                        job['public'] = {'state': 'error', 'message': '登录窗口已退出，原凭据保留'}
                    break
                await asyncio.sleep(.3)
        except (EOFError, OSError):
            if job['public']['state'] in ACTIVE:
                job['public'] = {'state': 'error', 'message': '登录窗口已关闭，原凭据保留'}
        finally:
            job['pipe'].close()
            await asyncio.to_thread(job['process'].join, 3)
            if job['process'].is_alive():
                job['process'].terminate()
                await asyncio.to_thread(job['process'].join, 3)

    async def _cancel(self, platform):
        job = self.jobs.get(platform)
        if job:
            job['public'] = {'state': 'cancelled', 'message': '已取消，原凭据保留'}
            job['stop'].set()
            if 'task' in job:
                try:
                    await asyncio.wait_for(asyncio.shield(job['task']), 5)
                except asyncio.TimeoutError:
                    job['process'].terminate()
                    await job['task']
            self.results[platform] = job['public']
            self.jobs.pop(platform, None)

    async def cancel(self, platform):
        async with self.lock(platform):
            await self._cancel(platform)
            return self.public(platform)

    async def disconnect(self, platform):
        async with self.lock(platform):
            await self._cancel(platform)
            # Only this known, application-owned profile path can be removed.
            profile = Path(BROWSER_DIR).resolve() / platform
            if profile.parent != Path(BROWSER_DIR).resolve() or platform not in KEYS:
                raise ValueError('无效平台')
            if profile.exists():
                await asyncio.to_thread(shutil.rmtree, profile)
            update(**{KEYS[platform]: ''})
            self.results[platform] = {'state': 'public' if platform == 'taoguba' else 'unconfigured',
                                      'message': '已断开；本地会话已清除'}
            return self.public(platform)

    async def validate(self, platform):
        async with self.lock(platform):
            credential = load_config().get(KEYS[platform], '')
            result = await asyncio.to_thread(validate_credential, platform, credential)
            if load_config().get(KEYS[platform], '') != credential:
                result = {'state': 'pending', 'message': '凭据已更新，请重新验证'}
            self.results[platform] = result
            job = self.jobs.get(platform)
            if job and job['public']['state'] not in ACTIVE:
                job['public'] = result
            return result

    async def close(self):
        for platform in list(self.jobs):
            await self.cancel(platform)

manager = LoginManager()
