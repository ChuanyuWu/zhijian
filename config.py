"""Thread-safe configuration with atomic replacement and partial updates."""
import copy
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

BASE_DIR = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get('ZHIJIAN_DATA_DIR') or os.path.join(
    os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else BASE_DIR, 'data')
CONFIG_PATH = os.path.join(DATA_DIR, 'config.json')
DB_PATH = os.path.join(DATA_DIR, 'posts.db')
BROWSER_DIR = os.path.join(DATA_DIR, 'browser_profiles')
DEFAULT_CONFIG = dict(weibo_cookie='', taoguba_cookie='', zsxq_token='',
                      weread_cookie='', access_password='', poll_interval_sec=120, users=[])
SECRET_KEYS = ('weibo_cookie', 'taoguba_cookie', 'zsxq_token', 'weread_cookie', 'access_password')
_lock = threading.RLock()

def _read():
    if not os.path.exists(CONFIG_PATH):
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, encoding='utf-8-sig') as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            raise ValueError('配置必须为对象')
    except (ValueError, OSError) as exc:
        raise RuntimeError('配置文件无法读取；请从本地备份恢复，程序未覆盖原文件') from exc
    return copy.deepcopy(DEFAULT_CONFIG) | cfg

def _write(cfg):
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix='config-', suffix='.tmp', dir=DATA_DIR)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(path, CONFIG_PATH)
    finally:
        if os.path.exists(path):
            os.unlink(path)

def load_config():
    with _lock:
        return _read()

def mutate_config(fn):
    with _lock:
        cfg = _read()
        fn(cfg)
        _write(cfg)
        return copy.deepcopy(cfg)

def update(**kwargs):
    return mutate_config(lambda cfg: cfg.update(kwargs))

def replace_credential(key, expected, value):
    changed = False
    def apply(cfg):
        nonlocal changed
        if cfg.get(key, '') == expected:
            cfg[key] = value
            changed = True
    mutate_config(apply)
    return changed

def save_config(cfg):
    with _lock:
        _write(copy.deepcopy(cfg))

def get(key, default=None):
    return load_config().get(key, default)
