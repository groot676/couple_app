import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import DATABASE_URL, DB_PATH

USING_POSTGRES = bool(DATABASE_URL)

if USING_POSTGRES:
    import psycopg
    from psycopg.rows import dict_row


# --- connection --------------------------------------------------------------

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


def _q(sql: str) -> str:
    """Translate '?' placeholders to psycopg's '%s' on the Postgres branch."""
    return sql.replace("?", "%s") if USING_POSTGRES else sql


def _utcnow() -> str:
    # Stored as ISO strings on both backends (SQLite has no datetime type;
    # Postgres TIMESTAMPTZ parses ISO). Avoids Python 3.12's deprecated
    # sqlite3 datetime adapters.
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_ts(value) -> datetime | None:
    """Normalize a stored timestamp (str from SQLite, datetime from psycopg)
    to an aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --- migrations ---------------------------------------------------------------
# Sequential, per-backend. v1 is idempotent (CREATE IF NOT EXISTS) so databases
# created before the migration runner existed adopt cleanly: they run v1 as a
# no-op, then v2 adds the new columns with lane='unsorted' — exactly the state
# the startup backfill expects.

_MIGRATIONS: list[dict[str, list[str]]] = [
    {  # v1 — original capture table
        "sqlite": [
            """CREATE TABLE IF NOT EXISTS items (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                text         TEXT    NOT NULL,
                sender_name  TEXT    NOT NULL,
                sender_id    INTEGER NOT NULL,
                chat_id      INTEGER NOT NULL,
                created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            "CREATE INDEX IF NOT EXISTS idx_items_created_at ON items(created_at DESC)",
        ],
        "postgres": [
            """CREATE TABLE IF NOT EXISTS items (
                id           BIGSERIAL PRIMARY KEY,
                text         TEXT      NOT NULL,
                sender_name  TEXT      NOT NULL,
                sender_id    BIGINT    NOT NULL,
                chat_id      BIGINT    NOT NULL,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )""",
            "CREATE INDEX IF NOT EXISTS idx_items_created_at ON items(created_at DESC)",
        ],
    },
    {  # v2 — lanes, money fields, settings, overrule signals
        "sqlite": [
            "ALTER TABLE items ADD COLUMN lane TEXT NOT NULL DEFAULT 'unsorted'",
            "ALTER TABLE items ADD COLUMN display_text TEXT",
            "ALTER TABLE items ADD COLUMN estimated_price INTEGER",
            "ALTER TABLE items ADD COLUMN priority INTEGER",
            "ALTER TABLE items ADD COLUMN status TEXT NOT NULL DEFAULT 'active'",
            "ALTER TABLE items ADD COLUMN done_at TIMESTAMP",
            "ALTER TABLE items ADD COLUMN done_by TEXT",
            "ALTER TABLE items ADD COLUMN done_price INTEGER",
            "ALTER TABLE items ADD COLUMN llm_raw TEXT",
            "ALTER TABLE items ADD COLUMN tg_message_id BIGINT",
            """CREATE TABLE IF NOT EXISTS settings (
                id              INTEGER PRIMARY KEY CHECK (id = 1),
                p1_name         TEXT,
                p1_income       INTEGER,
                p1_payday       INTEGER,
                p1_sender_id    BIGINT,
                p2_name         TEXT,
                p2_income       INTEGER,
                p2_payday       INTEGER,
                p2_sender_id    BIGINT,
                baseline        INTEGER,
                currency_code   TEXT NOT NULL DEFAULT 'CZK',
                currency_symbol TEXT NOT NULL DEFAULT 'Kč',
                timezone        TEXT NOT NULL DEFAULT 'Europe/Prague',
                updated_at      TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS overrules (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id      BIGINT NOT NULL,
                field        TEXT   NOT NULL,
                old_value    TEXT,
                new_value    TEXT,
                source       TEXT   NOT NULL,
                corrected_by BIGINT,
                created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
        ],
        "postgres": [
            "ALTER TABLE items ADD COLUMN lane TEXT NOT NULL DEFAULT 'unsorted'",
            "ALTER TABLE items ADD COLUMN display_text TEXT",
            "ALTER TABLE items ADD COLUMN estimated_price INTEGER",
            "ALTER TABLE items ADD COLUMN priority INTEGER",
            "ALTER TABLE items ADD COLUMN status TEXT NOT NULL DEFAULT 'active'",
            "ALTER TABLE items ADD COLUMN done_at TIMESTAMPTZ",
            "ALTER TABLE items ADD COLUMN done_by TEXT",
            "ALTER TABLE items ADD COLUMN done_price INTEGER",
            "ALTER TABLE items ADD COLUMN llm_raw TEXT",
            "ALTER TABLE items ADD COLUMN tg_message_id BIGINT",
            """CREATE TABLE IF NOT EXISTS settings (
                id              INTEGER PRIMARY KEY CHECK (id = 1),
                p1_name         TEXT,
                p1_income       INTEGER,
                p1_payday       INTEGER,
                p1_sender_id    BIGINT,
                p2_name         TEXT,
                p2_income       INTEGER,
                p2_payday       INTEGER,
                p2_sender_id    BIGINT,
                baseline        INTEGER,
                currency_code   TEXT NOT NULL DEFAULT 'CZK',
                currency_symbol TEXT NOT NULL DEFAULT 'Kč',
                timezone        TEXT NOT NULL DEFAULT 'Europe/Prague',
                updated_at      TIMESTAMPTZ
            )""",
            """CREATE TABLE IF NOT EXISTS overrules (
                id           BIGSERIAL PRIMARY KEY,
                item_id      BIGINT NOT NULL,
                field        TEXT   NOT NULL,
                old_value    TEXT,
                new_value    TEXT,
                source       TEXT   NOT NULL,
                corrected_by BIGINT,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )""",
        ],
    },
    {  # v3 — pot activation: the pot goes live at the first payday after setup
        "sqlite": ["ALTER TABLE settings ADD COLUMN setup_completed_at TIMESTAMP"],
        "postgres": ["ALTER TABLE settings ADD COLUMN setup_completed_at TIMESTAMPTZ"],
    },
]


def init_db() -> None:
    backend = "postgres" if USING_POSTGRES else "sqlite"
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS schema_meta (version INTEGER NOT NULL)")
        row = cur.execute("SELECT version FROM schema_meta").fetchone()
        if row is None:
            cur.execute("INSERT INTO schema_meta (version) VALUES (0)")
            version = 0
        else:
            version = row["version"]
        for target, migration in enumerate(_MIGRATIONS, start=1):
            if target <= version:
                continue
            for stmt in migration[backend]:
                cur.execute(stmt)
            cur.execute(_q("UPDATE schema_meta SET version = ?"), (target,))


# --- items --------------------------------------------------------------------

_ITEM_COLS = (
    "id, text, sender_name, sender_id, chat_id, created_at, lane, display_text, "
    "estimated_price, priority, status, done_at, done_by, done_price, tg_message_id"
)


def add_item(
    text: str,
    sender_name: str,
    sender_id: int,
    chat_id: int,
    *,
    lane: str = "unsorted",
    display_text: str | None = None,
    estimated_price: int | None = None,
    priority: int | None = None,
    llm_raw: str | None = None,
    tg_message_id: int | None = None,
) -> int:
    params = (
        text, sender_name, sender_id, chat_id,
        lane, display_text, estimated_price, priority, llm_raw, tg_message_id,
    )
    sql = (
        "INSERT INTO items (text, sender_name, sender_id, chat_id, lane, "
        "display_text, estimated_price, priority, llm_raw, tg_message_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    with _get_conn() as conn:
        cur = conn.cursor()
        if USING_POSTGRES:
            cur.execute(_q(sql) + " RETURNING id", params)
            return cur.fetchone()["id"]
        cur.execute(sql, params)
        return cur.lastrowid


def get_item(item_id: int) -> dict | None:
    with _get_conn() as conn:
        row = conn.cursor().execute(
            _q(f"SELECT {_ITEM_COLS} FROM items WHERE id = ?"), (item_id,)
        ).fetchone()
        return dict(row) if row else None


def _fetch_items(where: str, params: tuple, order: str) -> list[dict]:
    with _get_conn() as conn:
        rows = conn.cursor().execute(
            _q(f"SELECT {_ITEM_COLS} FROM items WHERE {where} ORDER BY {order}"),
            params,
        ).fetchall()
        return [dict(r) for r in rows]


def list_dreams() -> list[dict]:
    return _fetch_items(
        "lane = ? AND status = ?", ("dream", "active"),
        "COALESCE(priority, 0) DESC, created_at DESC, id DESC",
    )


def list_everyday() -> list[dict]:
    return _fetch_items(
        "lane = ? AND status = ?", ("everyday", "active"),
        "created_at DESC, id DESC",
    )


def list_unsorted() -> list[dict]:
    return _fetch_items(
        "lane = ? AND status = ?", ("unsorted", "active"),
        "created_at ASC, id ASC",
    )


def list_made_real() -> list[dict]:
    return _fetch_items(
        "lane = ? AND status = ?", ("dream", "done"),
        "done_at DESC, id DESC",
    )


def items_for_message(chat_id: int, tg_message_id: int) -> list[dict]:
    return _fetch_items(
        "chat_id = ? AND tg_message_id = ?", (chat_id, tg_message_id),
        "id ASC",
    )


# Legacy alias — the pre-v2 web app listed everything newest-first.
def list_items(limit: int = 200) -> list[dict]:
    items = _fetch_items("status = ?", ("active",), "created_at DESC, id DESC")
    return items[:limit]


def set_lane(item_id: int, lane: str) -> None:
    with _get_conn() as conn:
        conn.cursor().execute(
            _q("UPDATE items SET lane = ? WHERE id = ?"), (lane, item_id)
        )


def set_price(item_id: int, price: int | None) -> None:
    with _get_conn() as conn:
        conn.cursor().execute(
            _q("UPDATE items SET estimated_price = ? WHERE id = ?"), (price, item_id)
        )


def set_sorted(
    item_id: int,
    lane: str,
    display_text: str,
    estimated_price: int | None,
    priority: int | None,
    llm_raw: str,
) -> None:
    with _get_conn() as conn:
        conn.cursor().execute(
            _q(
                "UPDATE items SET lane = ?, display_text = ?, estimated_price = ?, "
                "priority = ?, llm_raw = ? WHERE id = ?"
            ),
            (lane, display_text, estimated_price, priority, llm_raw, item_id),
        )


def mark_done(item_id: int, done_by: str | None) -> None:
    # done_price snapshots the estimate at done-time so a later price edit
    # can't rewrite the pot's history.
    with _get_conn() as conn:
        conn.cursor().execute(
            _q(
                "UPDATE items SET status = 'done', done_at = ?, done_by = ?, "
                "done_price = estimated_price WHERE id = ? AND status = 'active'"
            ),
            (_utcnow(), done_by, item_id),
        )


def mark_removed(item_id: int) -> None:
    with _get_conn() as conn:
        conn.cursor().execute(
            _q("UPDATE items SET status = 'removed' WHERE id = ? AND status = 'active'"),
            (item_id,),
        )


def distinct_senders(limit: int = 2) -> list[tuple[int, str]]:
    """First-seen distinct (sender_id, sender_name) pairs from captures."""
    with _get_conn() as conn:
        rows = conn.cursor().execute(
            "SELECT id, sender_id, sender_name FROM items ORDER BY id ASC"
        ).fetchall()
    seen: dict[int, str] = {}
    for r in rows:
        seen.setdefault(r["sender_id"], r["sender_name"])
        if len(seen) >= limit:
            break
    return list(seen.items())


# --- settings -----------------------------------------------------------------

_SETTINGS_FIELDS = [
    "p1_name", "p1_income", "p1_payday", "p1_sender_id",
    "p2_name", "p2_income", "p2_payday", "p2_sender_id",
    "baseline", "currency_code", "currency_symbol", "timezone",
]


def get_settings() -> dict | None:
    with _get_conn() as conn:
        row = conn.cursor().execute("SELECT * FROM settings WHERE id = 1").fetchone()
        return dict(row) if row else None


def save_settings(fields: dict) -> None:
    """Upsert the single household row; merges with existing values."""
    clean = {k: v for k, v in fields.items() if k in _SETTINGS_FIELDS}
    current = get_settings() or {}
    was_complete = setup_complete(current) if current else False
    merged = {**{k: current.get(k) for k in _SETTINGS_FIELDS}, **clean}
    # Preserve NOT NULL defaults if the form ever sends blanks
    merged["currency_code"] = merged.get("currency_code") or "CZK"
    merged["currency_symbol"] = merged.get("currency_symbol") or "Kč"
    merged["timezone"] = merged.get("timezone") or "Europe/Prague"
    cols = ", ".join(_SETTINGS_FIELDS)
    with _get_conn() as conn:
        cur = conn.cursor()
        if current:
            assignments = ", ".join(f"{k} = ?" for k in _SETTINGS_FIELDS)
            cur.execute(
                _q(f"UPDATE settings SET {assignments}, updated_at = ? WHERE id = 1"),
                tuple(merged[k] for k in _SETTINGS_FIELDS) + (_utcnow(),),
            )
        else:
            placeholders = ", ".join("?" for _ in _SETTINGS_FIELDS)
            cur.execute(
                _q(
                    f"INSERT INTO settings (id, {cols}, updated_at) "
                    f"VALUES (1, {placeholders}, ?)"
                ),
                tuple(merged[k] for k in _SETTINGS_FIELDS) + (_utcnow(),),
            )
        # Stamp the moment setup first became complete — the pot activates at
        # the first payday after this.
        if not was_complete and setup_complete(merged):
            cur.execute(
                _q("UPDATE settings SET setup_completed_at = ? WHERE id = 1 AND setup_completed_at IS NULL"),
                (_utcnow(),),
            )


def setup_complete(settings: dict | None) -> bool:
    """The pot and ready-marks stay silent until everything money-math needs exists."""
    if not settings:
        return False
    required = ["p1_name", "p1_income", "p1_payday", "p2_name", "p2_income", "p2_payday", "baseline"]
    return all(settings.get(k) is not None and settings.get(k) != "" for k in required)


def auto_attach_sender(sender_id: int) -> None:
    """Attach a partner's telegram sender_id to an empty slot (post-setup,
    when the second partner texts for the first time)."""
    s = get_settings()
    if not s:
        return
    if s.get("p1_sender_id") == sender_id or s.get("p2_sender_id") == sender_id:
        return
    if s.get("p1_sender_id") is None:
        save_settings({"p1_sender_id": sender_id})
    elif s.get("p2_sender_id") is None:
        save_settings({"p2_sender_id": sender_id})


# --- overrule signals -----------------------------------------------------------

def add_overrule(
    item_id: int,
    field: str,
    old_value: str | None,
    new_value: str | None,
    source: str,
    corrected_by: int | None = None,
) -> None:
    with _get_conn() as conn:
        conn.cursor().execute(
            _q(
                "INSERT INTO overrules (item_id, field, old_value, new_value, source, corrected_by) "
                "VALUES (?, ?, ?, ?, ?, ?)"
            ),
            (item_id, field, old_value, new_value, source, corrected_by),
        )
