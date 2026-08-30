"""پایگاه‌داده‌ی محلی SQLite برای ربات پروتکل تشخیص دوگانه.

جایگزین ذخیره‌سازی ابری: آرشیو سناریوها و تاریخچه‌ی سطح همراه
فقط روی همین سرور در فایل data/bot.db نگه داشته می‌شود (بدون هیچ هاست بیرونی).
داده‌ی حساسِ پایش خطر عمداً اینجا ذخیره نمی‌شود و در user_data می‌ماند.
"""
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "bot.db")
_LOCK = threading.Lock()


@contextmanager
def _db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    """ساخت جداول در صورت نبود."""
    with _LOCK, _db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS scenario_archive(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            sid TEXT NOT NULL,
            title TEXT NOT NULL,
            answer TEXT,
            analysis TEXT,
            score INTEGER)""")
        c.execute("""CREATE TABLE IF NOT EXISTS level_history(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            points REAL NOT NULL)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_arch_uid ON scenario_archive(user_id, id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_lvl_uid ON level_history(user_id, id)")


def add_scenario(uid: int, sid: str, title: str, answer: str, analysis: str, score) -> None:
    """ثبت یک پاسخ سناریو + تحلیل؛ نگه‌داشتن حداکثر ۵۰ ردیف آخر هر کاربر."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with _LOCK, _db() as c:
        c.execute(
            "INSERT INTO scenario_archive(user_id, created_at, sid, title, answer, analysis, score)"
            " VALUES(?,?,?,?,?,?,?)",
            (uid, now, sid, title, (answer or "")[:200], (analysis or "")[:400], score))
        c.execute(
            "DELETE FROM scenario_archive WHERE user_id=? AND id NOT IN ("
            "  SELECT id FROM scenario_archive WHERE user_id=? ORDER BY id DESC LIMIT 50)",
            (uid, uid))


def scenarios(uid: int, limit: int = 50) -> list:
    """ردیف‌های آرشیو کاربر، قدیمی→جدید."""
    with _LOCK, _db() as c:
        rows = c.execute(
            "SELECT created_at, sid, title, answer, analysis, score"
            " FROM scenario_archive WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (uid, limit)).fetchall()
    return [{"date": r[0], "sid": r[1], "title": r[2],
             "answer": r[3] or "", "analysis": r[4] or "", "score": r[5]}
            for r in reversed(rows)]


def add_level(uid: int, pts: float) -> None:
    """ثبت نمره‌ی سطح همراهی؛ نگه‌داشتن حداکثر ۳۰ ردیف آخر هر کاربر."""
    with _LOCK, _db() as c:
        c.execute("INSERT INTO level_history(user_id, created_at, points) VALUES(?,?,?)",
                  (uid, datetime.now().strftime("%Y-%m-%d"), float(pts)))
        c.execute(
            "DELETE FROM level_history WHERE user_id=? AND id NOT IN ("
            "  SELECT id FROM level_history WHERE user_id=? ORDER BY id DESC LIMIT 30)",
            (uid, uid))


def levels(uid: int, limit: int = 30) -> list:
    with _LOCK, _db() as c:
        rows = c.execute(
            "SELECT created_at, points FROM level_history"
            " WHERE user_id=? ORDER BY id DESC LIMIT ?", (uid, limit)).fetchall()
    return [{"date": r[0], "pts": r[1]} for r in reversed(rows)]


def wipe(uid: int) -> int:
    """حذف کامل داده‌های یک کاربر (حق حذف، هم‌راستا با 42 CFR Part 2). تعداد ردیف‌های حذف‌شده را برمی‌گرداند."""
    with _LOCK, _db() as c:
        n = (c.execute("SELECT COUNT(*) FROM scenario_archive WHERE user_id=?", (uid,)).fetchone()[0]
             + c.execute("SELECT COUNT(*) FROM level_history WHERE user_id=?", (uid,)).fetchone()[0])
        c.execute("DELETE FROM scenario_archive WHERE user_id=?", (uid,))
        c.execute("DELETE FROM level_history WHERE user_id=?", (uid,))
    return n


def migrate(user_data_map) -> int:
    """مهاجرت یک‌باره‌ی داده‌های قدیمیِ user_data (pickle) به SQLite.
    user_data_map = application.user_data؛ فلگ «_db_migrated» روی هر کاربر می‌گذارد."""
    moved = 0
    with _LOCK, _db() as c:
        for uid, ud in (user_data_map or {}).items():
            if not isinstance(ud, dict) or ud.get("_db_migrated"):
                continue
            for e in ud.get("scenario_archive") or []:
                c.execute(
                    "INSERT INTO scenario_archive(user_id, created_at, sid, title, answer, analysis, score)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (uid, e.get("date", ""), e.get("sid", ""), e.get("title", ""),
                     (e.get("answer") or "")[:200], (e.get("analysis") or "")[:400],
                     e.get("score")))
                moved += 1
            for h in ud.get("level_history") or []:
                c.execute("INSERT INTO level_history(user_id, created_at, points) VALUES(?,?,?)",
                          (uid, h.get("date", ""), float(h.get("pts") or 0)))
            ud["_db_migrated"] = True
    return moved
