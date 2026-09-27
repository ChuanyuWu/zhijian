"""SQLite 存储：帖子表 + 去重 + 搜索。"""
import json
import sqlite3
import threading
from datetime import datetime

from config import DB_PATH, DATA_DIR
import os

_lock = threading.Lock()
os.makedirs(DATA_DIR, exist_ok=True)


def _conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _lock, _conn() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,          -- weibo / taoguba / zsxq
            author_id TEXT NOT NULL,
            author_name TEXT DEFAULT '',
            post_uid TEXT NOT NULL UNIQUE,   -- platform:author_id:post_id 去重键
            post_id TEXT NOT NULL,
            title TEXT DEFAULT '',
            content_html TEXT DEFAULT '',    -- 正文（安全 HTML，含 <br>）
            images TEXT DEFAULT '[]',        -- JSON 数组：图片 URL 列表
            url TEXT DEFAULT '',             -- 原文链接
            published_at TEXT DEFAULT '',    -- ISO 时间
            created_at TEXT DEFAULT ''       -- 入库时间
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_posts_time ON posts(published_at DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_posts_platform ON posts(platform)")
        c.commit()


def insert_post(p: dict) -> bool:
    """插入帖子，已存在则忽略。返回 True 表示是新帖。"""
    sql = """INSERT OR IGNORE INTO posts
        (platform, author_id, author_name, post_uid, post_id, title,
         content_html, images, url, published_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)"""
    with _lock, _conn() as c:
        cur = c.execute(sql, (
            p["platform"], p["author_id"], p.get("author_name", ""),
            p["post_uid"], p["post_id"], p.get("title", ""),
            p.get("content_html", ""), json.dumps(p.get("images", []), ensure_ascii=False),
            p.get("url", ""), p.get("published_at", ""),
            datetime.now().isoformat(timespec="seconds"),
        ))
        c.commit()
        return cur.rowcount > 0


def query_posts(platform=None, author_id=None, keyword=None, offset=0, limit=50):
    sql = "SELECT * FROM posts WHERE 1=1"
    args = []
    if platform:
        sql += " AND platform=?"
        args.append(platform)
    if author_id:
        sql += " AND platform || ':' || author_id = ?"
        args.append(author_id)
    if keyword:
        sql += " AND (content_html LIKE ? OR title LIKE ? OR author_name LIKE ?)"
        kw = f"%{keyword}%"
        args += [kw, kw, kw]
    sql += " ORDER BY published_at DESC, id DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    with _lock, _conn() as c:
        rows = [dict(r) for r in c.execute(sql, args).fetchall()]
    for r in rows:
        r["images"] = json.loads(r["images"] or "[]")
    return rows


def count_posts():
    with _lock, _conn() as c:
        return c.execute("SELECT COUNT(*) FROM posts").fetchone()[0]


def delete_post(post_id: int) -> bool:
    with _lock, _conn() as c:
        cur = c.execute("DELETE FROM posts WHERE id=?", (post_id,))
        return cur.rowcount > 0


def get_empty_content_posts(platform, author_id, limit=3, within_days=7):
    """找该用户需要回填正文的帖子：
    - 近期只有标题（content_html 为空）
    - 或正文含 placeHolder 水印（v1 旧数据，不限时间，全部重抓）
    """
    with _lock, _conn() as c:
        rows = c.execute("""
            SELECT post_uid, post_id FROM posts
            WHERE platform=? AND author_id=?
              AND (content_html LIKE '%placeHolder%'
                   OR (content_html='' AND created_at >= datetime('now', ?)))
            ORDER BY id DESC LIMIT ?
        """, (platform, author_id, f"-{within_days} days", limit)).fetchall()
    return [dict(r) for r in rows]


def update_post_content(post_uid, content_html, images, published_at=""):
    with _lock, _conn() as c:
        c.execute("""
            UPDATE posts SET content_html=?, images=?,
                published_at = CASE WHEN ? != '' THEN ? ELSE published_at END
            WHERE post_uid=?
        """, (content_html, json.dumps(images, ensure_ascii=False),
              published_at, published_at, post_uid))
        c.commit()


def distinct_authors():
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT DISTINCT platform, author_id, author_name FROM posts ORDER BY platform"
        ).fetchall()]
