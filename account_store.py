"""SQLite 账号、对话归属与请求额度；供少量邀请用户试用。"""
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

DB_PATH = Path(__file__).resolve().parent / "data" / "companion.db"
DAILY_LIMIT = 20
ITERATIONS = 600_000


@contextmanager
def database():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_database():
    with database() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                salt TEXT NOT NULL, password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY, title TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                owner_id TEXT REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                position INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(conversation_id, position)
            );
            CREATE TABLE IF NOT EXISTS daily_usage (
                user_id TEXT NOT NULL REFERENCES users(id), day TEXT NOT NULL,
                used INTEGER NOT NULL DEFAULT 0, last_request REAL NOT NULL DEFAULT 0,
                PRIMARY KEY(user_id, day)
            );
            CREATE TABLE IF NOT EXISTS login_limits (
                username TEXT PRIMARY KEY, attempts INTEGER NOT NULL, started REAL NOT NULL
            );
        """)
        # 升级旧数据库：旧记录保持无归属，不自动分配给第一个注册的人。
        columns = {row[1] for row in conn.execute("PRAGMA table_info(conversations)")}
        if "owner_id" not in columns:
            conn.execute("ALTER TABLE conversations ADD COLUMN owner_id TEXT REFERENCES users(id)")
        conn.execute("CREATE INDEX IF NOT EXISTS chats_owner ON conversations(owner_id)")


def password_digest(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS).hex()


def register(username, password, invite, expected_invite):
    if not expected_invite or not hmac.compare_digest(invite.encode(), expected_invite.encode()):
        raise ValueError("邀请码无效，或管理员尚未开放注册。")
    username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9_]{4,32}", username):
        raise ValueError("用户名请使用 4～32 位英文字母、数字或下划线。")
    if not 10 <= len(password) <= 128:
        raise ValueError("密码长度需要为 10～128 个字符。")
    salt = secrets.token_hex(16)
    digest = password_digest(password, salt)
    try:
        with database() as conn:
            conn.execute("INSERT INTO users(id, username, salt, password_hash) VALUES (?, ?, ?, ?)",
                         (uuid4().hex, username, salt, digest))
    except sqlite3.IntegrityError:
        raise ValueError("该用户名不可用，请换一个。") from None


def login(username, password):
    username = username.strip().lower()[:128]
    now = time.time()
    with database() as conn:
        # 失败次数写在数据库中，刷新页面不会重置。
        conn.execute("BEGIN IMMEDIATE")
        limit = conn.execute("SELECT attempts, started FROM login_limits WHERE username = ?", (username,)).fetchone()
        if limit and now - limit[1] < 900 and limit[0] >= 10:
            raise ValueError("登录尝试过多，请在 15 分钟后重试。")
        started = limit[1] if limit and now - limit[1] < 900 else now
        attempts = limit[0] + 1 if started != now else 1
        conn.execute("INSERT OR REPLACE INTO login_limits VALUES (?, ?, ?)", (username, attempts, started))
        row = conn.execute("SELECT id, salt, password_hash FROM users WHERE username = ?", (username,)).fetchone()
    # 即使账号不存在也执行相同哈希操作；不在持有写锁时计算哈希。
    salt, expected = (row[1], row[2]) if row else ("00" * 16, "00" * 32)
    matches = hmac.compare_digest(password_digest(password[:129], salt), expected)
    if not row or not matches:
        raise ValueError("用户名或密码错误。")
    with database() as conn:
        conn.execute("DELETE FROM login_limits WHERE username = ?", (username,))
    return {"id": row[0], "username": username}


def store_chat(user_id, chat_id, title, messages):
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        owner = conn.execute("SELECT owner_id FROM conversations WHERE id = ?", (chat_id,)).fetchone()
        if owner and owner[0] != user_id:
            raise PermissionError("无权修改此对话。")
        conn.execute("""INSERT INTO conversations(id, title, owner_id) VALUES (?, ?, ?)
                     ON CONFLICT(id) DO UPDATE SET title = excluded.title""", (chat_id, title, user_id))
        conn.executemany("""INSERT INTO messages(conversation_id, position, role, content) VALUES (?, ?, ?, ?)
            ON CONFLICT(conversation_id, position) DO UPDATE SET role = excluded.role, content = excluded.content""",
            [(chat_id, i, m["role"], m["content"]) for i, m in enumerate(messages)])
        conn.execute("DELETE FROM messages WHERE conversation_id = ? AND position >= ?", (chat_id, len(messages)))


def load_chats(user_id):
    with database() as conn:
        chats = {r[0]: {"title": r[1], "messages": []} for r in conn.execute(
            "SELECT id, title FROM conversations WHERE owner_id = ? ORDER BY rowid", (user_id,))}
        for chat_id, role, content in conn.execute("""SELECT m.conversation_id, m.role, m.content
            FROM messages m JOIN conversations c ON c.id = m.conversation_id
            WHERE c.owner_id = ? ORDER BY c.rowid, m.position""", (user_id,)):
            chats[chat_id]["messages"].append({"role": role, "content": content})
    return chats


def today():
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def used_today(user_id):
    with database() as conn:
        row = conn.execute("SELECT used FROM daily_usage WHERE user_id = ? AND day = ?", (user_id, today())).fetchone()
    return row[0] if row else 0


def reserve_request(user_id):
    """请求前原子扣次数；超时、失败也计次，避免中断后反复消耗 API。"""
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        day, now = today(), time.time()
        conn.execute("INSERT OR IGNORE INTO daily_usage(user_id, day) VALUES (?, ?)", (user_id, day))
        row = conn.execute("SELECT used, last_request FROM daily_usage WHERE user_id = ? AND day = ?", (user_id, day)).fetchone()
        if row[0] >= DAILY_LIMIT:
            raise ValueError("今天的 20 次请求已用完，请明天再来。")
        if now - row[1] < 5:
            raise ValueError("请求太快，请间隔至少 5 秒再发送。")
        conn.execute("UPDATE daily_usage SET used = used + 1, last_request = ? WHERE user_id = ? AND day = ?",
                     (now, user_id, day))
