# -*- coding: utf-8 -*-
"""
SQLite database layer for the admin panel.

Manages:
- kiro_accounts: Kiro session tokens (your accounts)
- api_clients: Client API keys you distribute
- usage_logs: Per-request logs (tokens, model, client, account, cost)
"""

import hashlib
import hmac
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional

DB_PATH = Path("admin.db")


def get_db_path() -> Path:
    return DB_PATH


@contextmanager
def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS kiro_accounts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                label       TEXT NOT NULL,
                api_key     TEXT NOT NULL,
                email       TEXT,
                enabled     INTEGER NOT NULL DEFAULT 1,
                created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                last_used   INTEGER,
                requests    INTEGER NOT NULL DEFAULT 0,
                failures    INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS api_clients (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                api_key     TEXT NOT NULL UNIQUE,
                enabled     INTEGER NOT NULL DEFAULT 1,
                note        TEXT,
                created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                last_used   INTEGER
            );

            CREATE TABLE IF NOT EXISTS usage_logs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                ts              INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                client_id       INTEGER REFERENCES api_clients(id),
                client_name     TEXT,
                account_id      TEXT,
                account_label   TEXT,
                model           TEXT,
                input_tokens    INTEGER NOT NULL DEFAULT 0,
                output_tokens   INTEGER NOT NULL DEFAULT 0,
                total_tokens    INTEGER NOT NULL DEFAULT 0,
                status          TEXT NOT NULL DEFAULT 'ok',
                endpoint        TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_usage_ts        ON usage_logs(ts);
            CREATE INDEX IF NOT EXISTS idx_usage_client    ON usage_logs(client_id);
            CREATE INDEX IF NOT EXISTS idx_usage_account   ON usage_logs(account_id);
        """)


# ---------------------------------------------------------------------------
# Kiro accounts
# ---------------------------------------------------------------------------

def list_kiro_accounts() -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM kiro_accounts ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]


def add_kiro_account(label: str, api_key: str, email: Optional[str] = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO kiro_accounts (label, api_key, email) VALUES (?,?,?)",
            (label, api_key, email)
        )
        return cur.lastrowid


def update_kiro_account(account_id: int, label: str, api_key: str,
                        email: Optional[str], enabled: bool) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            """UPDATE kiro_accounts
               SET label=?, api_key=?, email=?, enabled=?
               WHERE id=?""",
            (label, api_key, email, int(enabled), account_id)
        )
        return cur.rowcount > 0


def delete_kiro_account(account_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM kiro_accounts WHERE id=?", (account_id,))
        return cur.rowcount > 0


def toggle_kiro_account(account_id: int, enabled: bool) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE kiro_accounts SET enabled=? WHERE id=?",
            (int(enabled), account_id)
        )
        return cur.rowcount > 0


def mark_kiro_account_used(account_id: int, success: bool = True) -> None:
    with get_conn() as conn:
        if success:
            conn.execute(
                "UPDATE kiro_accounts SET last_used=strftime('%s','now'), requests=requests+1 WHERE id=?",
                (account_id,)
            )
        else:
            conn.execute(
                "UPDATE kiro_accounts SET failures=failures+1 WHERE id=?",
                (account_id,)
            )


def get_enabled_kiro_accounts() -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM kiro_accounts WHERE enabled=1 ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]


def set_kiro_account_email(account_id: int, email: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE kiro_accounts SET email=? WHERE id=?",
            (email, account_id)
        )


# ---------------------------------------------------------------------------
# API clients
# ---------------------------------------------------------------------------

def list_api_clients() -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM api_clients ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]


def generate_api_key() -> str:
    return "kgw-" + secrets.token_urlsafe(32)


def add_api_client(name: str, note: str = "") -> Dict:
    key = generate_api_key()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO api_clients (name, api_key, note) VALUES (?,?,?)",
            (name, key, note)
        )
        return {"id": cur.lastrowid, "name": name, "api_key": key, "note": note}


def delete_api_client(client_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM api_clients WHERE id=?", (client_id,))
        return cur.rowcount > 0


def toggle_api_client(client_id: int, enabled: bool) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE api_clients SET enabled=? WHERE id=?",
            (int(enabled), client_id)
        )
        return cur.rowcount > 0


def get_client_by_key(api_key: str) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM api_clients WHERE api_key=? AND enabled=1",
            (api_key,)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE api_clients SET last_used=strftime('%s','now') WHERE id=?",
                (row["id"],)
            )
            return dict(row)
        return None


# ---------------------------------------------------------------------------
# Usage logs
# ---------------------------------------------------------------------------

def log_usage(
    client_id: Optional[int],
    client_name: Optional[str],
    account_id: Optional[str],
    account_label: Optional[str],
    model: str,
    input_tokens: int,
    output_tokens: int,
    status: str = "ok",
    endpoint: str = "",
) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO usage_logs
               (client_id, client_name, account_id, account_label, model,
                input_tokens, output_tokens, total_tokens, status, endpoint)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (client_id, client_name, account_id, account_label, model,
             input_tokens, output_tokens, input_tokens + output_tokens,
             status, endpoint)
        )


def get_usage_summary() -> Dict:
    with get_conn() as conn:
        today = int(time.time()) - 86400
        week = int(time.time()) - 604800

        total = conn.execute(
            "SELECT COUNT(*) as reqs, SUM(total_tokens) as tokens FROM usage_logs WHERE status='ok'"
        ).fetchone()
        today_row = conn.execute(
            "SELECT COUNT(*) as reqs, SUM(total_tokens) as tokens FROM usage_logs WHERE status='ok' AND ts>=?",
            (today,)
        ).fetchone()
        week_row = conn.execute(
            "SELECT COUNT(*) as reqs, SUM(total_tokens) as tokens FROM usage_logs WHERE status='ok' AND ts>=?",
            (week,)
        ).fetchone()
        by_model = conn.execute(
            """SELECT model, COUNT(*) as reqs, SUM(total_tokens) as tokens
               FROM usage_logs WHERE status='ok'
               GROUP BY model ORDER BY tokens DESC LIMIT 10"""
        ).fetchall()
        by_client = conn.execute(
            """SELECT client_name, COUNT(*) as reqs, SUM(total_tokens) as tokens
               FROM usage_logs WHERE status='ok'
               GROUP BY client_name ORDER BY tokens DESC LIMIT 10"""
        ).fetchall()
        by_account = conn.execute(
            """SELECT account_label, COUNT(*) as reqs, SUM(total_tokens) as tokens
               FROM usage_logs WHERE status='ok'
               GROUP BY account_label ORDER BY tokens DESC LIMIT 10"""
        ).fetchall()

        return {
            "total": {"requests": total["reqs"] or 0, "tokens": total["tokens"] or 0},
            "today": {"requests": today_row["reqs"] or 0, "tokens": today_row["tokens"] or 0},
            "week": {"requests": week_row["reqs"] or 0, "tokens": week_row["tokens"] or 0},
            "by_model": [dict(r) for r in by_model],
            "by_client": [dict(r) for r in by_client],
            "by_account": [dict(r) for r in by_account],
        }


def get_recent_logs(limit: int = 100, client_id: Optional[int] = None) -> List[Dict]:
    with get_conn() as conn:
        if client_id:
            rows = conn.execute(
                "SELECT * FROM usage_logs WHERE client_id=? ORDER BY ts DESC LIMIT ?",
                (client_id, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM usage_logs ORDER BY ts DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Admin password (stored as SHA-256 hash in settings table)
# ---------------------------------------------------------------------------

def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def is_first_setup() -> bool:
    """Returns True if no admin password has been set yet."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key='admin_password_hash'"
        ).fetchone()
        return row is None


def set_admin_password(password: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('admin_password_hash', ?)",
            (_hash_password(password),)
        )


def verify_admin_password(password: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key='admin_password_hash'"
        ).fetchone()
        if not row:
            return False
        return hmac.compare_digest(row["value"], _hash_password(password))
