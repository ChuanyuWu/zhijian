"""配置管理：所有设置存 data/config.json，界面可改。"""
import json
import os
import sys
import threading

if getattr(sys, "frozen", False):
    # PyInstaller 打包后：static 等只读资源在 exe 内部临时目录(_MEIPASS)，
    # 数据(data/)放在 exe 同级目录，方便用户查看和备份
    BASE_DIR = sys._MEIPASS
    DATA_DIR = os.path.join(os.path.dirname(sys.executable), "data")
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = os.path.join(BASE_DIR, "data")

CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
DB_PATH = os.path.join(DATA_DIR, "posts.db")

DEFAULT_CONFIG = {
    # ---- 凭证 ----
    "weibo_cookie": "",        # 微博 Cookie（从 m.weibo.cn 抓，含 SUB= 字段）
    "taoguba_cookie": "",      # 淘股吧 Cookie（可选，公开内容不登录也能抓）
    "zsxq_token": "",          # 知识星球 zsxq_access_token
    "weread_cookie": "",       # 公众号·微信读书书架桥：网页版 Cookie（含 wr_vid 等）

    # ---- 轮询 ----
    "poll_interval_sec": 120,  # 轮询间隔（秒），建议 60~300

    # ---- 访问密码 ----
    "access_password": "",     # 留空 = 本地模式不启用密码；部署到公网务必设置

    # ---- 监控用户 ----
    # platform: weibo / taoguba / zsxq（zsxq 预留）
    # user_id:  微博填数字 uid；淘股吧填博客页 /blog/xxxx 里的数字；zsxq 预留
    "users": [
        # {"platform": "weibo", "user_id": "2803301701", "nickname": "人民日报", "enabled": True},
        # {"platform": "taoguba", "user_id": "3493426", "nickname": "远山1982", "enabled": True},
        # {"platform": "zsxq", "user_id": "星球ID或用户ID", "nickname": "预留", "enabled": False},
    ],
}

_lock = threading.Lock()


def load_config() -> dict:
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    # 补全新版本新增字段
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    return cfg


def save_config(cfg: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    with _lock:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)


def get(key, default=None):
    return load_config().get(key, default)


def update(**kwargs):
    cfg = load_config()
    cfg.update(kwargs)
    save_config(cfg)
    return cfg
