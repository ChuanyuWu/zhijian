"""Build an offline-capable Windows folder with console and silent launchers."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def main():
    if os.name != 'nt':
        raise SystemExit('请在 Windows 上打包')
    os.chdir(ROOT)
    browsers = ROOT / 'browsers'
    env = os.environ | {'PLAYWRIGHT_BROWSERS_PATH': str(browsers)}
    if not list(browsers.glob('chromium-*/chrome-win64/chrome.exe')):
        subprocess.run([sys.executable, '-m', 'playwright', 'install', 'chromium'], env=env, check=True)
    common = [sys.executable, '-m', 'PyInstaller', '--onedir', '--clean', '--noconfirm',
              '--add-data', 'static;static', '--add-data', 'browsers;browsers',
              '--collect-all', 'playwright', '--hidden-import', 'bleach', 'main.py']
    subprocess.run(common[:-1] + ['--name', 'zhijian', common[-1]], check=True)
    # Same entry point and dependencies: both launchers share the same _internal folder.
    subprocess.run(common[:-1] + ['--name', 'zhijian_silent', '--windowed', common[-1]], check=True)
    release = ROOT / 'dist' / 'zhijian'
    shutil.copy2(ROOT / 'dist' / 'zhijian_silent' / 'zhijian_silent.exe', release)
    for name in ('README.md', 'SHARING.md', 'exe_autostart_on.bat', 'exe_autostart_off.bat'):
        shutil.copy2(ROOT / name, release)
    shutil.make_archive(str(ROOT / 'dist' / 'zhijian-windows'), 'zip', release.parent, release.name)
    print('完成：dist/zhijian-windows.zip（不含个人 data）')

if __name__ == '__main__':
    main()
