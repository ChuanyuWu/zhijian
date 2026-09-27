"""启动自愈：每次启动后端时自动修复历史数据问题，无需手动跑脚本。

目前包含：
1. 微博旧帖链接清洗（v1 时期入库的相对链接 -> 绝对地址，纯本地处理）
2. 淘股吧水印帖自动发现并依靠"正文回填"机制逐轮重抓
   （回填条件见 database.get_empty_content_posts，含 placeHolder 即视为缺正文）
"""
import sqlite3

from config import DB_PATH
from crawlers.weibo import WeiboCrawler


def repair_weibo_links() -> int:
    """清洗微博旧帖里的相对链接。返回修复条数。"""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT post_uid, content_html FROM posts WHERE platform='weibo'"
    ).fetchall()
    fixed = 0
    for uid, content in rows:
        if not content:
            continue
        needs_fix = ('href="/' in content or 'href="//' in content
                     or "<script" in content or 'target="_blank"' not in content)
        if not needs_fix:
            continue
        new_content = WeiboCrawler._clean_html(content)
        if new_content != content:
            conn.execute("UPDATE posts SET content_html=? WHERE post_uid=?",
                         (new_content, uid))
            fixed += 1
    conn.commit()
    conn.close()
    return fixed


def run_startup_repairs():
    try:
        n = repair_weibo_links()
        if n:
            print(f"[自愈] 修复了 {n} 条微博旧帖的链接")
    except Exception as e:
        # 自愈失败绝不影响主程序启动
        print(f"[自愈] 微博旧帖修复跳过：{e}")
