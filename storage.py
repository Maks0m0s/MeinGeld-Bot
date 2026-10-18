import os
import shutil
import sqlite3
import threading
import time

DB_PATH = "monobank_control.db"

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS users (
        chat_id INTEGER PRIMARY KEY,
        created_at INTEGER NOT NULL,
        authorized INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS transactions (
        tx_key TEXT PRIMARY KEY,
        account_id TEXT NOT NULL,
        amount INTEGER NOT NULL,
        currency TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        tx_time INTEGER NOT NULL,
        category_id INTEGER,
        annotated_at INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS operations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category_id INTEGER NOT NULL,
        delta_uah_kop INTEGER NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        ref_tx_key TEXT,
        created_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS accounts_state (
        account_id TEXT PRIMARY KEY,
        last_ts INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        chat_id INTEGER NOT NULL,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        PRIMARY KEY (chat_id, key)
    )
    """,
]

MIGRATED_TABLES = ("categories", "transactions", "operations", "accounts_state")


class Storage:
    def __init__(self, db_path=DB_PATH):
        self._lock = threading.RLock()
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.executescript(";\n".join(SCHEMA))
            self._migrate()
            self.conn.commit()

    # ---------- migration: per-chat budget -> one shared budget ----------

    def _has_column(self, table, column):
        cols = [row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")]
        return column in cols

    def _migrate(self):
        if not self._has_column("categories", "chat_id"):
            return

        self._backup()

        self.conn.execute("BEGIN")
        try:
            for table in MIGRATED_TABLES:
                self.conn.execute(f"ALTER TABLE {table} RENAME TO {table}_old")
            for stmt in SCHEMA:
                self.conn.execute(stmt)

            cat_map = self._migrate_categories()
            self._migrate_operations(cat_map)
            self._migrate_transactions(cat_map)
            self._migrate_accounts_state()

            for table in MIGRATED_TABLES:
                self.conn.execute(f"DROP TABLE {table}_old")
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def _backup(self):
        try:
            root, ext = os.path.splitext(self.db_path)
            shutil.copyfile(self.db_path, f"{root}_backup{ext or '.db'}")
        except OSError:
            pass

    def _migrate_categories(self):
        cat_map = {}
        name_to_id = {}
        for row in self.conn.execute("SELECT id, name FROM categories_old ORDER BY id"):
            existing = name_to_id.get(row["name"])
            if existing is None:
                self.conn.execute(
                    "INSERT INTO categories (id, name) VALUES (?, ?)", (row["id"], row["name"])
                )
                name_to_id[row["name"]] = row["id"]
                existing = row["id"]
            cat_map[row["id"]] = existing
        return cat_map

    def _migrate_operations(self, cat_map):
        for row in self.conn.execute("SELECT * FROM operations_old ORDER BY id"):
            cat_id = cat_map.get(row["category_id"])
            if cat_id is None:
                continue
            self.conn.execute(
                "INSERT INTO operations (id, category_id, delta_uah_kop, note, ref_tx_key, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (row["id"], cat_id, row["delta_uah_kop"], row["note"],
                 row["ref_tx_key"], row["created_at"]),
            )
        self.conn.execute(
            """
            DELETE FROM operations
            WHERE ref_tx_key IS NOT NULL
              AND id NOT IN (
                  SELECT MIN(id) FROM operations WHERE ref_tx_key IS NOT NULL GROUP BY ref_tx_key
              )
            """
        )

    def _migrate_transactions(self, cat_map):
        selected = {}
        for row in self.conn.execute("SELECT * FROM transactions_old ORDER BY rowid"):
            prev = selected.get(row["tx_key"])
            if prev is None or (prev["category_id"] is None and row["category_id"] is not None):
                selected[row["tx_key"]] = row
        for row in selected.values():
            cat_id = row["category_id"]
            new_cat = cat_map.get(cat_id) if cat_id is not None else None
            if cat_id is not None and new_cat is None:
                new_cat, annotated_at = None, None
            else:
                annotated_at = row["annotated_at"]
            self.conn.execute(
                "INSERT INTO transactions (tx_key, account_id, amount, currency, description,"
                " tx_time, category_id, annotated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (row["tx_key"], row["account_id"], row["amount"], row["currency"],
                 row["description"], row["tx_time"], new_cat, annotated_at),
            )

    def _migrate_accounts_state(self):
        rows = self.conn.execute(
            "SELECT account_id, MAX(last_ts) AS ts FROM accounts_state_old GROUP BY account_id"
        ).fetchall()
        for row in rows:
            self.conn.execute(
                "INSERT INTO accounts_state (account_id, last_ts) VALUES (?, ?)",
                (row["account_id"], row["ts"]),
            )

    # ---------- users (per chat) ----------

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

    # ---------- budget (shared by every user) ----------

    def add_category(self, name):
        with self._lock:
            try:
                cur = self.conn.execute(
                    "INSERT INTO categories (name) VALUES (?)", (name.strip(),)
                )
                self.conn.commit()
                return cur.lastrowid
            except sqlite3.IntegrityError:
                return None

    def get_categories(self):
        with self._lock:
            return self.conn.execute(
                "SELECT id, name FROM categories ORDER BY id"
            ).fetchall()

    def get_category(self, category_id):
        with self._lock:
            return self.conn.execute(
                "SELECT id, name FROM categories WHERE id = ?", (category_id,)
            ).fetchone()

    def get_category_by_name(self, name):
        with self._lock:
            return self.conn.execute(
                "SELECT id, name FROM categories WHERE name = ?", (name.strip(),)
            ).fetchone()

    def delete_category(self, category_id):
        with self._lock:
            self.conn.execute(
                "DELETE FROM operations WHERE category_id = ?", (category_id,)
            )
            self.conn.execute(
                "DELETE FROM categories WHERE id = ?", (category_id,)
            )
            self.conn.commit()

    def get_category_balances(self):
        with self._lock:
            return self.conn.execute(
                """
                SELECT c.id, c.name, COALESCE(SUM(o.delta_uah_kop), 0) AS balance
                FROM categories c
                LEFT JOIN operations o ON o.category_id = c.id
                GROUP BY c.id
                ORDER BY c.id
                """
            ).fetchall()

    def add_operation(self, category_id, delta_uah_kop, note="", ref_tx_key=None):
        with self._lock:
            self.conn.execute(
                "INSERT INTO operations (category_id, delta_uah_kop, note, ref_tx_key, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (category_id, delta_uah_kop, note, ref_tx_key, int(time.time())),
            )
            self.conn.commit()

    def save_transaction(self, tx_key, account_id, amount, currency, description, tx_time):
        with self._lock:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO transactions (tx_key, account_id, amount, currency, description, tx_time)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (tx_key, account_id, amount, currency, description, tx_time),
            )
            self.conn.commit()

    def is_transaction_new(self, tx_key):
        with self._lock:
            row = self.conn.execute(
                "SELECT 1 FROM transactions WHERE tx_key = ?", (tx_key,)
            ).fetchone()
            return row is None

    def annotate_transaction(self, tx_key, category_id):
        with self._lock:
            self.conn.execute(
                "UPDATE transactions SET category_id = ?, annotated_at = ? WHERE tx_key = ?",
                (category_id, int(time.time()), tx_key),
            )
            self.conn.commit()

    def get_transaction(self, tx_key):
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM transactions WHERE tx_key = ?", (tx_key,)
            ).fetchone()

    def get_recent_transactions(self, limit=10):
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM transactions ORDER BY tx_time DESC LIMIT ?", (limit,)
            ).fetchall()

    def get_unannotated_transactions(self):
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM transactions WHERE category_id IS NULL ORDER BY tx_time DESC"
            ).fetchall()

    def get_account_last_ts(self, account_id):
        with self._lock:
            row = self.conn.execute(
                "SELECT last_ts FROM accounts_state WHERE account_id = ?", (account_id,)
            ).fetchone()
            return row["last_ts"] if row else None

    def set_account_last_ts(self, account_id, ts):
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO accounts_state (account_id, last_ts) VALUES (?, ?)
                ON CONFLICT(account_id) DO UPDATE SET last_ts = excluded.last_ts
                """,
                (account_id, int(ts)),
            )
            self.conn.commit()

    def unannotate_last_for_category(self, category_id):
        with self._lock:
            row = self.conn.execute(
                """
                SELECT id FROM operations
                WHERE category_id = ? AND ref_tx_key IS NOT NULL
                ORDER BY id DESC LIMIT 1
                """,
                (category_id,),
            ).fetchone()
            if row is None:
                return None
            op = self.conn.execute("SELECT * FROM operations WHERE id = ?", (row["id"],)).fetchone()
            self.conn.execute("DELETE FROM operations WHERE id = ?", (row["id"],))
            self.conn.execute(
                "UPDATE transactions SET category_id = NULL, annotated_at = NULL WHERE tx_key = ?",
                (op["ref_tx_key"],),
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
