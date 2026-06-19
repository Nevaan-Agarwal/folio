"""Database bootstrap utilities for PostgreSQL/SQLite."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

try:
    import psycopg
except Exception:  # pragma: no cover - optional dependency
    psycopg = None


def _normalize_sqlite_path(database_url: str) -> str:
    if database_url.startswith("sqlite:///"):
        return database_url.removeprefix("sqlite:///")
    if database_url.startswith("sqlite://"):
        return database_url.removeprefix("sqlite://")
    return database_url


@contextmanager
def _connect(database_url: str):
    if database_url.startswith("postgresql://") or database_url.startswith("postgres://"):
        if psycopg is None:
            raise RuntimeError(
                "PostgreSQL URL configured but psycopg is not installed. "
                "Install with: pip install \"psycopg[binary]\""
            )
        with psycopg.connect(database_url) as conn:
            yield conn, True
        return

    sqlite_path = _normalize_sqlite_path(database_url) or "folio.db"
    Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(sqlite_path)
    try:
        yield conn, False
        conn.commit()
    finally:
        conn.close()


def run_sql_file(database_url: str, sql_file_path: str) -> None:
    sql_text = Path(sql_file_path).read_text(encoding="utf-8")
    with _connect(database_url) as (conn, is_postgres):
        cur = conn.cursor()
        if is_postgres:
            cur.execute(sql_text)
        else:
            cur.executescript(sql_text)


def run_smoke_transaction(database_url: str, user_id: str = "smoke_user_001") -> dict:
    created_at = datetime.now(timezone.utc).isoformat()
    with _connect(database_url) as (conn, is_postgres):
        cur = conn.cursor()
        if is_postgres:
            cur.execute(
                """
                INSERT INTO users (id, first_name, surname, email, role, language, disabled, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    first_name = EXCLUDED.first_name,
                    surname = EXCLUDED.surname,
                    email = EXCLUDED.email,
                    role = EXCLUDED.role,
                    language = EXCLUDED.language,
                    disabled = EXCLUDED.disabled
                """,
                (
                    user_id,
                    "Smoke",
                    "Test",
                    f"{user_id}@folio.local",
                    "employee",
                    "en",
                    False,
                    created_at,
                ),
            )
            cur.execute(
                """
                INSERT INTO audit_logs (user_id, action, timestamp, details, ip_address, user_agent, session_id)
                VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s)
                """,
                (
                    user_id,
                    "user_registered",
                    created_at,
                    '{"source":"app_db_smoke","status":"ok"}',
                    "127.0.0.1",
                    "flask-cli",
                    "smoke-session",
                ),
            )
            cur.execute("SELECT id, email, role, disabled FROM users WHERE id = %s", (user_id,))
            user_row = cur.fetchone()
            cur.execute(
                "SELECT id, user_id, action FROM audit_logs WHERE user_id = %s ORDER BY id DESC LIMIT 1",
                (user_id,),
            )
            audit_row = cur.fetchone()
        else:
            cur.execute(
                """
                INSERT INTO users (id, first_name, surname, email, role, language, disabled, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    first_name = excluded.first_name,
                    surname = excluded.surname,
                    email = excluded.email,
                    role = excluded.role,
                    language = excluded.language,
                    disabled = excluded.disabled
                """,
                (
                    user_id,
                    "Smoke",
                    "Test",
                    f"{user_id}@folio.local",
                    "employee",
                    "en",
                    0,
                    created_at,
                ),
            )
            cur.execute(
                """
                INSERT INTO audit_logs (user_id, action, timestamp, details, ip_address, user_agent, session_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    "user_registered",
                    created_at,
                    '{"source":"app_db_smoke","status":"ok"}',
                    "127.0.0.1",
                    "flask-cli",
                    "smoke-session",
                ),
            )
            cur.execute("SELECT id, email, role, disabled FROM users WHERE id = ?", (user_id,))
            user_row = cur.fetchone()
            cur.execute(
                "SELECT id, user_id, action FROM audit_logs WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                (user_id,),
            )
            audit_row = cur.fetchone()

    return {
        "user": {
            "id": user_row[0] if user_row else None,
            "email": user_row[1] if user_row else None,
            "role": user_row[2] if user_row else None,
            "disabled": user_row[3] if user_row else None,
        },
        "audit": {
            "id": audit_row[0] if audit_row else None,
            "user_id": audit_row[1] if audit_row else None,
            "action": audit_row[2] if audit_row else None,
        },
    }
