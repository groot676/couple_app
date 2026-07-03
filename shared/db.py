import sqlite3
from contextlib import contextmanager

from .config import DATABASE_URL, DB_PATH

USING_POSTGRES = bool(DATABASE_URL)

if USING_POSTGRES:
    import psycopg
    from psycopg.rows import dict_row


_SCHEMA_SQLITE = [
    """
    CREATE TABLE IF NOT EXISTS items (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        text         TEXT    NOT NULL,
        sender_name  TEXT    NOT NULL,
        sender_id    INTEGER NOT NULL,
        chat_id      INTEGER NOT NULL,
        created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_items_created_at ON items(created_at DESC)",
]

_SCHEMA_POSTGRES = [
    """
    CREATE TABLE IF NOT EXISTS items (
        id           BIGSERIAL PRIMARY KEY,
        text         TEXT      NOT NULL,
        sender_name  TEXT      NOT NULL,
        sender_id    BIGINT    NOT NULL,
        chat_id      BIGINT    NOT NULL,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_items_created_at ON items(created_at DESC)",
]


@contextmanager
def _get_conn():
    if USING_POSTGRES:
        conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    schema = _SCHEMA_POSTGRES if USING_POSTGRES else _SCHEMA_SQLITE
    with _get_conn() as conn:
        cur = conn.cursor()
        for stmt in schema:
            cur.execute(stmt)


def add_item(text: str, sender_name: str, sender_id: int, chat_id: int) -> int:
    with _get_conn() as conn:
        cur = conn.cursor()
        if USING_POSTGRES:
            cur.execute(
                "INSERT INTO items (text, sender_name, sender_id, chat_id) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (text, sender_name, sender_id, chat_id),
            )
            return cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO items (text, sender_name, sender_id, chat_id) "
            "VALUES (?, ?, ?, ?)",
            (text, sender_name, sender_id, chat_id),
        )
        return cur.lastrowid


def list_items(limit: int = 200) -> list[dict]:
    with _get_conn() as conn:
        cur = conn.cursor()
        if USING_POSTGRES:
            cur.execute(
                "SELECT id, text, sender_name, sender_id, chat_id, created_at "
                "FROM items ORDER BY created_at DESC, id DESC LIMIT %s",
                (limit,),
            )
            return list(cur.fetchall())
        rows = cur.execute(
            "SELECT id, text, sender_name, sender_id, chat_id, created_at "
            "FROM items ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
