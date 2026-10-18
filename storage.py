import sqlite3
import threading
import time

DB_PATH = "monobank_control.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    chat_id INTEGER PRIMARY KEY,
    created_at INTEGER NOT NULL,
    authorized INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    UNIQUE(chat_id, name)
);

CREATE TABLE IF NOT EXISTS transactions (
    tx_key TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    account_id TEXT NOT NULL,
    amount INTEGER NOT NULL,
    currency TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    tx_time INTEGER NOT NULL,
    category_id INTEGER,
    annotated_at INTEGER,
    PRIMARY KEY (tx_key, chat_id)
);

CREATE TABLE IF NOT EXISTS operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    category_id INTEGER NOT NULL,
    delta_uah_kop INTEGER NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    ref_tx_key TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts_state (
    account_id TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    last_ts INTEGER NOT NULL,
    PRIMARY KEY (account_id, chat_id)
);

CREATE TABLE IF NOT EXISTS settings (
    chat_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (chat_id, key)
);
"""


class Storage:
    def __init__(self, db_path=DB_PATH):
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.executescript(SCHEMA)
            self._migrate()
            self.conn.commit()

    def _migrate(self):
        cols = [row["name"] for row in self.conn.execute("PRAGMA table_info(users)")]
        if "authorized" not in cols:
            self.conn.execute(
                "ALTER TABLE users ADD COLUMN authorized INTEGER NOT NULL DEFAULT 0"
            )

    def add_user(self, chat_id):
        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO users (chat_id, created_at, authorized) VALUES (?, ?, 0)",
                (chat_id, int(time.time())),
            )
            self.conn.commit()

    def is_authorized(self, chat_id):
        with self._lock:
            row = self.conn.execute(
                "SELECT authorized FROM users WHERE chat_id = ?", (chat_id,)
            ).fetchone()
            return bool(row and row["authorized"])

    def set_authorized(self, chat_id, value=1):
        with self._lock:
            self.conn.execute(
                "UPDATE users SET authorized = ? WHERE chat_id = ?",
                (1 if value else 0, chat_id),
            )
            self.conn.commit()

    def get_users(self, authorized_only=True):
        with self._lock:
            query = "SELECT chat_id FROM users"
            if authorized_only:
                query += " WHERE authorized = 1"
            return [row["chat_id"] for row in self.conn.execute(query)]

    def add_category(self, chat_id, name):
        with self._lock:
            try:
                cur = self.conn.execute(
                    "INSERT INTO categories (chat_id, name) VALUES (?, ?)",
                    (chat_id, name.strip()),
                )
                self.conn.commit()
                return cur.lastrowid
            except sqlite3.IntegrityError:
                return None

    def get_categories(self, chat_id):
        with self._lock:
            return self.conn.execute(
                "SELECT id, name FROM categories WHERE chat_id = ? ORDER BY id", (chat_id,)
            ).fetchall()

    def get_category(self, chat_id, category_id):
        with self._lock:
            return self.conn.execute(
                "SELECT id, name FROM categories WHERE chat_id = ? AND id = ?",
                (chat_id, category_id),
            ).fetchone()

    def get_category_by_name(self, chat_id, name):
        with self._lock:
            return self.conn.execute(
                "SELECT id, name FROM categories WHERE chat_id = ? AND name = ?",
                (chat_id, name.strip()),
            ).fetchone()

    def delete_category(self, chat_id, category_id):
        with self._lock:
            self.conn.execute(
                "DELETE FROM operations WHERE chat_id = ? AND category_id = ?",
                (chat_id, category_id),
            )
            self.conn.execute(
                "DELETE FROM categories WHERE chat_id = ? AND id = ?",
                (chat_id, category_id),
            )
            self.conn.commit()

    def get_category_balances(self, chat_id):
        with self._lock:
            return self.conn.execute(
                """
                SELECT c.id, c.name, COALESCE(SUM(o.delta_uah_kop), 0) AS balance
                FROM categories c
                LEFT JOIN operations o ON o.category_id = c.id AND o.chat_id = c.chat_id
                WHERE c.chat_id = ?
                GROUP BY c.id
                ORDER BY c.id
                """,
                (chat_id,),
            ).fetchall()

    def add_operation(self, chat_id, category_id, delta_uah_kop, note="", ref_tx_key=None):
        with self._lock:
            self.conn.execute(
                "INSERT INTO operations (chat_id, category_id, delta_uah_kop, note, ref_tx_key, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (chat_id, category_id, delta_uah_kop, note, ref_tx_key, int(time.time())),
            )
            self.conn.commit()

    def save_transaction(self, tx_key, chat_id, account_id, amount, currency, description, tx_time):
        with self._lock:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO transactions (tx_key, chat_id, account_id, amount, currency, description, tx_time)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (tx_key, chat_id, account_id, amount, currency, description, tx_time),
            )
            self.conn.commit()

    def is_transaction_new(self, tx_key, chat_id):
        with self._lock:
            row = self.conn.execute(
                "SELECT 1 FROM transactions WHERE tx_key = ? AND chat_id = ?",
                (tx_key, chat_id),
            ).fetchone()
            return row is None

    def annotate_transaction(self, tx_key, chat_id, category_id):
        with self._lock:
            self.conn.execute(
                "UPDATE transactions SET category_id = ?, annotated_at = ? WHERE tx_key = ? AND chat_id = ?",
                (category_id, int(time.time()), tx_key, chat_id),
            )
            self.conn.commit()

    def get_transaction(self, tx_key, chat_id):
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM transactions WHERE tx_key = ? AND chat_id = ?",
                (tx_key, chat_id),
            ).fetchone()

    def get_recent_transactions(self, chat_id, limit=10):
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM transactions WHERE chat_id = ? ORDER BY tx_time DESC LIMIT ?",
                (chat_id, limit),
            ).fetchall()

    def get_unannotated_transactions(self, chat_id):
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM transactions WHERE chat_id = ? AND category_id IS NULL ORDER BY tx_time DESC",
                (chat_id,),
            ).fetchall()

    def get_account_last_ts(self, account_id, chat_id):
        with self._lock:
            row = self.conn.execute(
                "SELECT last_ts FROM accounts_state WHERE account_id = ? AND chat_id = ?",
                (account_id, chat_id),
            ).fetchone()
            return row["last_ts"] if row else None

    def set_account_last_ts(self, account_id, chat_id, ts):
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO accounts_state (account_id, chat_id, last_ts) VALUES (?, ?, ?)
                ON CONFLICT(account_id, chat_id) DO UPDATE SET last_ts = excluded.last_ts
                """,
                (account_id, chat_id, int(ts)),
            )
            self.conn.commit()

    def unannotate_last_for_category(self, chat_id, category_id):
        with self._lock:
            row = self.conn.execute(
                """
                SELECT id FROM operations
                WHERE chat_id = ? AND category_id = ? AND ref_tx_key IS NOT NULL
                ORDER BY id DESC LIMIT 1
                """,
                (chat_id, category_id),
            ).fetchone()
            if row is None:
                return None
            op = self.conn.execute("SELECT * FROM operations WHERE id = ?", (row["id"],)).fetchone()
            self.conn.execute("DELETE FROM operations WHERE id = ?", (row["id"],))
            self.conn.execute(
                "UPDATE transactions SET category_id = NULL, annotated_at = NULL WHERE tx_key = ? AND chat_id = ?",
                (op["ref_tx_key"], chat_id),
            )
            self.conn.commit()
            return op

    def get_setting(self, chat_id, key, default=None):
        with self._lock:
            row = self.conn.execute(
                "SELECT value FROM settings WHERE chat_id = ? AND key = ?",
                (chat_id, key),
            ).fetchone()
            return row["value"] if row else default

    def set_setting(self, chat_id, key, value):
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO settings (chat_id, key, value) VALUES (?, ?, ?)
                ON CONFLICT(chat_id, key) DO UPDATE SET value = excluded.value
                """,
                (chat_id, key, str(value)),
            )
            self.conn.commit()
