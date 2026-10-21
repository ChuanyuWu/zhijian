"""Exercise frozen startup, shared dependencies and the real helper process."""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import httpx
ROOT = Path(__file__).resolve().parents[1]

def main():
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'dist' / 'zhijian'
    for index, name in enumerate(('zhijian.exe', 'zhijian_silent.exe')):
        with tempfile.TemporaryDirectory(prefix='zhijian-tests-exe-', dir=ROOT) as temp:
            port = 18766 + index
            env = os.environ | {'ZHIJIAN_DATA_DIR':temp, 'ZHIJIAN_DISABLE_MONITOR':'1',
                                'ZHIJIAN_NO_BROWSER':'1', 'ZHIJIAN_PORT':str(port),
                                'ZHIJIAN_LOGIN_HEADLESS':'1'}
            process = subprocess.Popen([str(folder / name)], env=env, cwd=folder,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                with httpx.Client(base_url=f'http://127.0.0.1:{port}', trust_env=False, timeout=15) as c:
                    for _ in range(100):
                        try:
                            if c.get('/api/status').status_code==200:break
                        except httpx.HTTPError:pass
                        time.sleep(.2)
                    else:raise RuntimeError(name+' failed startup')
                    assert c.get('/').status_code == 200
                    assert c.get('/static/app.js').status_code == 200
                    if index == 1:
                        result = c.post('/api/connections/weibo/start', json={}).json()
                        assert result['state']=='starting',result
                        state = 'starting'
                        for _ in range(60):
                            result = c.get('/api/connections/weibo').json()
                            state = result['state']
                            if state != 'starting':break
                            time.sleep(1)
                        assert state == 'waiting',result
                        result = c.post('/api/connections/weibo/cancel', json={}).json()
                        assert result['state']=='cancelled',result
                        assert c.get('/api/status').status_code==200
                print(name+': PASS startup, static files'+(', frozen browser helper start/cancel' if index else ''),flush=True)
            finally:
                process.terminate();process.wait(timeout=15)
                # Give the driver time to release its browser profile after cancellation.
                time.sleep(.3)

if __name__=='__main__':main()
