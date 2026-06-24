"""Document-style repository API backed by the app's SQL tables."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

try:
    import psycopg
    from psycopg.types.json import Json
except Exception:  # pragma: no cover - optional dependency at runtime
    psycopg = None
    Json = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_sqlite_url(database_url: str) -> str:
    if database_url.startswith("sqlite:///"):
        return database_url.removeprefix("sqlite:///")
    if database_url.startswith("sqlite://"):
        return database_url.removeprefix("sqlite://")
    return database_url


TABLE_COLLECTIONS: dict[str, dict[str, Any]] = {
    "users": {
        "table": "users",
        "id": "id",
        "fields": {
            "firstName": "first_name",
            "surname": "surname",
            "email": "email",
            "passwordHash": "password_hash",
            "role": "role",
            "language": "language",
            "disabled": "disabled",
            "createdAt": "created_at",
        },
    },
    "receipts": {
        "table": "receipts",
        "id": "id",
        "fields": {
            "userId": "user_id",
            "imageUrl": "image_url",
            "uploadedAt": "uploaded_at",
            "ocrText": "ocr_text",
            "ocrConfidence": "ocr_confidence",
            "merchant": "merchant",
            "address": "address",
            "date": "receipt_date",
            "currency": "currency",
            "subtotal": "subtotal",
            "tax": "tax",
            "tip": "tip",
            "total": "total",
            "receiptNumber": "receipt_number",
            "processingStatus": "processing_status",
            "reviewStatus": "review_status",
            "errorMessage": "error_message",
            "pdfUrl": "pdf_url",
        },
    },
    "forms": {
        "table": "forms",
        "id": "id",
        "json": {"hostedPersons", "missingFields", "aiConfidence"},
        "fields": {
            "receiptId": "receipt_id",
            "userId": "user_id",
            "type": "form_type",
            "expenseCategory": "expense_category",
            "host": "host",
            "hostedPersons": "hosted_persons",
            "occasion": "occasion",
            "dateOfHospitality": "date_of_hospitality",
            "locationOfHospitality": "location_of_hospitality",
            "invoiceAmount": "invoice_amount",
            "tip": "tip",
            "totalAmount": "total_amount",
            "merchant": "merchant",
            "receiptNumber": "receipt_number",
            "date": "form_date",
            "place": "place",
            "missingFields": "missing_fields",
            "needsManualReview": "needs_manual_review",
            "aiConfidence": "ai_confidence",
            "status": "status",
            "rejectionReason": "rejection_reason",
            "createdAt": "created_at",
            "updatedAt": "updated_at",
        },
    },
    "combined_documents": {
        "table": "combined_documents",
        "id": "id",
        "fields": {
            "formId": "form_id",
            "receiptId": "receipt_id",
            "userId": "user_id",
            "filePath": "file_path",
            "downloadUrl": "download_url",
            "createdAt": "created_at",
            "emailSent": "email_sent",
            "emailSentAt": "email_sent_at",
            "emailMessageId": "email_message_id",
            "emailDeliveryStatus": "email_delivery_status",
            "emailError": "email_error",
            "userEmail": "user_email",
            "merchant": "merchant",
            "category": "category",
            "host": "host",
            "occasion": "occasion",
            "totalAmount": "total_amount",
            "currency": "currency",
            "status": "status",
        },
    },
    "auditLogs": {
        "table": "audit_logs",
        "id": "id",
        "auto_id": True,
        "json": {"details", "readBy"},
        "fields": {
            "userId": "user_id",
            "action": "action",
            "timestamp": "timestamp",
            "details": "details",
            "ipAddress": "ip_address",
            "userAgent": "user_agent",
            "sessionId": "session_id",
            "readBy": "read_by",
        },
    },
    "audit_logs": {
        "table": "audit_logs",
        "id": "id",
        "auto_id": True,
        "json": {"details", "readBy"},
        "fields": {
            "userId": "user_id",
            "action": "action",
            "timestamp": "timestamp",
            "details": "details",
            "ipAddress": "ip_address",
            "userAgent": "user_agent",
            "sessionId": "session_id",
            "readBy": "read_by",
        },
    },
    "analytics_cache": {
        "table": "analytics_cache",
        "id": "id",
        "json": {"data"},
        "fields": {
            "generatedAt": "generated_at",
            "startDate": "start_date",
            "endDate": "end_date",
            "data": "data",
        },
    },
}


class _SQLBackend:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self._sqlite_lock = threading.Lock()
        self._is_postgres = database_url.startswith("postgresql://") or database_url.startswith("postgres://")
        self._sqlite_path = ""
        if self._is_postgres and psycopg is None:
            raise RuntimeError(
                "psycopg is required for PostgreSQL DATABASE_URL but is not installed."
            )
        if not self._is_postgres:
            self._sqlite_path = _normalize_sqlite_url(self.database_url) or "folio.db"
            Path(self._sqlite_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect_sqlite(self):
        return sqlite3.connect(self._sqlite_path, check_same_thread=False)

    def _init_schema(self) -> None:
        if self._is_postgres:
            with psycopg.connect(self.database_url) as conn:
                with conn.cursor() as cur:
                    self._create_app_tables(cur)
                    self._create_generic_table(cur)
                conn.commit()
            return
        with self._sqlite_lock:
            conn = self._connect_sqlite()
            try:
                cur = conn.cursor()
                self._create_app_tables(cur)
                self._create_generic_table(cur)
                conn.commit()
            finally:
                conn.close()

    def _create_generic_table(self, cur) -> None:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS documents ("
            "collection_name TEXT NOT NULL,"
            "doc_id TEXT NOT NULL,"
            "payload TEXT NOT NULL,"
            "updated_at TEXT NOT NULL,"
            "PRIMARY KEY (collection_name, doc_id)"
            ")"
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_collection ON documents(collection_name)")

    def _create_app_tables(self, cur) -> None:
        if self._is_postgres:
            statements = [
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    first_name TEXT NOT NULL,
                    surname TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL DEFAULT 'employee' CHECK (role IN ('employee', 'admin')),
                    language TEXT NOT NULL DEFAULT 'en',
                    disabled BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS receipts (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                    image_url TEXT NOT NULL,
                    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    ocr_text TEXT DEFAULT '',
                    ocr_confidence DOUBLE PRECISION,
                    merchant TEXT DEFAULT '',
                    address TEXT DEFAULT '',
                    receipt_date TEXT DEFAULT '',
                    currency TEXT DEFAULT '',
                    subtotal DOUBLE PRECISION,
                    tax DOUBLE PRECISION,
                    tip DOUBLE PRECISION,
                    total DOUBLE PRECISION,
                    receipt_number TEXT DEFAULT '',
                    processing_status TEXT DEFAULT 'uploaded',
                    review_status TEXT DEFAULT 'draft',
                    error_message TEXT DEFAULT '',
                    pdf_url TEXT DEFAULT ''
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS forms (
                    id TEXT PRIMARY KEY,
                    receipt_id TEXT NOT NULL UNIQUE REFERENCES receipts(id) ON DELETE RESTRICT,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                    form_type TEXT DEFAULT 'Hospitality Expense',
                    expense_category TEXT DEFAULT 'Other',
                    host TEXT DEFAULT '',
                    hosted_persons JSONB DEFAULT '[]'::jsonb,
                    occasion TEXT DEFAULT '',
                    date_of_hospitality TEXT,
                    location_of_hospitality TEXT DEFAULT '',
                    invoice_amount DOUBLE PRECISION,
                    tip DOUBLE PRECISION,
                    total_amount DOUBLE PRECISION,
                    merchant TEXT DEFAULT '',
                    receipt_number TEXT DEFAULT '',
                    form_date TEXT,
                    place TEXT DEFAULT '',
                    missing_fields JSONB DEFAULT '[]'::jsonb,
                    needs_manual_review BOOLEAN DEFAULT FALSE,
                    ai_confidence JSONB DEFAULT '{}'::jsonb,
                    status TEXT DEFAULT 'draft',
                    rejection_reason TEXT DEFAULT '',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS combined_documents (
                    id TEXT PRIMARY KEY,
                    form_id TEXT NOT NULL REFERENCES forms(id) ON DELETE RESTRICT,
                    receipt_id TEXT NOT NULL REFERENCES receipts(id) ON DELETE RESTRICT,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                    file_path TEXT NOT NULL,
                    download_url TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    email_sent BOOLEAN NOT NULL DEFAULT FALSE,
                    email_sent_at TIMESTAMPTZ,
                    email_message_id TEXT,
                    email_delivery_status TEXT DEFAULT 'pending',
                    email_error TEXT,
                    user_email TEXT DEFAULT '',
                    merchant TEXT DEFAULT '',
                    category TEXT DEFAULT 'Other',
                    host TEXT DEFAULT '',
                    occasion TEXT DEFAULT '',
                    total_amount DOUBLE PRECISION,
                    currency TEXT DEFAULT 'EUR',
                    status TEXT DEFAULT 'processing'
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id BIGSERIAL PRIMARY KEY,
                    user_id TEXT,
                    action TEXT NOT NULL,
                    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    details JSONB NOT NULL DEFAULT '{}'::jsonb,
                    ip_address TEXT NOT NULL DEFAULT '',
                    user_agent TEXT NOT NULL DEFAULT '',
                    session_id TEXT NOT NULL DEFAULT '',
                    read_by JSONB NOT NULL DEFAULT '[]'::jsonb
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS analytics_cache (
                    id TEXT PRIMARY KEY,
                    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    start_date TEXT NOT NULL DEFAULT '',
                    end_date TEXT NOT NULL DEFAULT '',
                    data JSONB NOT NULL DEFAULT '{}'::jsonb
                )
                """,
            ]
            for statement in statements:
                cur.execute(statement)
            alter_statements = [
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE receipts ADD COLUMN IF NOT EXISTS pdf_url TEXT DEFAULT ''",
                "ALTER TABLE forms ADD COLUMN IF NOT EXISTS rejection_reason TEXT DEFAULT ''",
                "ALTER TABLE combined_documents ADD COLUMN IF NOT EXISTS email_error TEXT",
                "ALTER TABLE combined_documents ADD COLUMN IF NOT EXISTS merchant TEXT DEFAULT ''",
                "ALTER TABLE combined_documents ADD COLUMN IF NOT EXISTS category TEXT DEFAULT 'Other'",
                "ALTER TABLE combined_documents ADD COLUMN IF NOT EXISTS host TEXT DEFAULT ''",
                "ALTER TABLE combined_documents ADD COLUMN IF NOT EXISTS occasion TEXT DEFAULT ''",
                "ALTER TABLE combined_documents ADD COLUMN IF NOT EXISTS total_amount DOUBLE PRECISION",
                "ALTER TABLE combined_documents ADD COLUMN IF NOT EXISTS currency TEXT DEFAULT 'EUR'",
                "ALTER TABLE combined_documents ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'processing'",
                "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS read_by JSONB NOT NULL DEFAULT '[]'::jsonb",
                "ALTER TABLE receipts ALTER COLUMN ocr_text DROP NOT NULL",
                "ALTER TABLE receipts ALTER COLUMN merchant DROP NOT NULL",
                "ALTER TABLE receipts ALTER COLUMN address DROP NOT NULL",
                "ALTER TABLE receipts ALTER COLUMN receipt_date DROP NOT NULL",
                "ALTER TABLE receipts ALTER COLUMN currency DROP NOT NULL",
                "ALTER TABLE receipts ALTER COLUMN receipt_number DROP NOT NULL",
                "ALTER TABLE receipts ALTER COLUMN processing_status DROP NOT NULL",
                "ALTER TABLE receipts ALTER COLUMN review_status DROP NOT NULL",
                "ALTER TABLE receipts ALTER COLUMN error_message DROP NOT NULL",
                "ALTER TABLE receipts ALTER COLUMN pdf_url DROP NOT NULL",
                "ALTER TABLE forms ALTER COLUMN form_type DROP NOT NULL",
                "ALTER TABLE forms ALTER COLUMN expense_category DROP NOT NULL",
                "ALTER TABLE forms ALTER COLUMN host DROP NOT NULL",
                "ALTER TABLE forms ALTER COLUMN hosted_persons DROP NOT NULL",
                "ALTER TABLE forms ALTER COLUMN occasion DROP NOT NULL",
                "ALTER TABLE forms ALTER COLUMN location_of_hospitality DROP NOT NULL",
                "ALTER TABLE forms ALTER COLUMN merchant DROP NOT NULL",
                "ALTER TABLE forms ALTER COLUMN receipt_number DROP NOT NULL",
                "ALTER TABLE forms ALTER COLUMN place DROP NOT NULL",
                "ALTER TABLE forms ALTER COLUMN missing_fields DROP NOT NULL",
                "ALTER TABLE forms ALTER COLUMN needs_manual_review DROP NOT NULL",
                "ALTER TABLE forms ALTER COLUMN ai_confidence DROP NOT NULL",
                "ALTER TABLE forms ALTER COLUMN status DROP NOT NULL",
                "ALTER TABLE forms ALTER COLUMN rejection_reason DROP NOT NULL",
                "ALTER TABLE combined_documents ALTER COLUMN email_delivery_status DROP NOT NULL",
                "ALTER TABLE combined_documents ALTER COLUMN user_email DROP NOT NULL",
                "ALTER TABLE combined_documents ALTER COLUMN merchant DROP NOT NULL",
                "ALTER TABLE combined_documents ALTER COLUMN category DROP NOT NULL",
                "ALTER TABLE combined_documents ALTER COLUMN host DROP NOT NULL",
                "ALTER TABLE combined_documents ALTER COLUMN occasion DROP NOT NULL",
                "ALTER TABLE combined_documents ALTER COLUMN currency DROP NOT NULL",
                "ALTER TABLE combined_documents ALTER COLUMN status DROP NOT NULL",
            ]
            for statement in alter_statements:
                cur.execute(statement)
            for statement in [
                "CREATE INDEX IF NOT EXISTS idx_receipts_user_id ON receipts(user_id)",
                "CREATE INDEX IF NOT EXISTS idx_forms_user_id ON forms(user_id)",
                "CREATE INDEX IF NOT EXISTS idx_forms_receipt_id ON forms(receipt_id)",
                "CREATE INDEX IF NOT EXISTS idx_combined_documents_user_id ON combined_documents(user_id)",
                "CREATE INDEX IF NOT EXISTS idx_combined_documents_form_id ON combined_documents(form_id)",
                "CREATE INDEX IF NOT EXISTS idx_combined_documents_receipt_id ON combined_documents(receipt_id)",
                "CREATE INDEX IF NOT EXISTS idx_audit_logs_user_id ON audit_logs(user_id)",
                "CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs(action)",
                "CREATE INDEX IF NOT EXISTS idx_audit_logs_timestamp ON audit_logs(timestamp DESC)",
            ]:
                cur.execute(statement)
            return

        statements = [
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                first_name TEXT NOT NULL,
                surname TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL DEFAULT 'employee',
                language TEXT NOT NULL DEFAULT 'en',
                disabled INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS receipts (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                image_url TEXT NOT NULL,
                uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                ocr_text TEXT NOT NULL DEFAULT '',
                ocr_confidence REAL,
                merchant TEXT NOT NULL DEFAULT '',
                address TEXT NOT NULL DEFAULT '',
                receipt_date TEXT NOT NULL DEFAULT '',
                currency TEXT NOT NULL DEFAULT '',
                subtotal REAL,
                tax REAL,
                tip REAL,
                total REAL,
                receipt_number TEXT NOT NULL DEFAULT '',
                processing_status TEXT NOT NULL DEFAULT 'uploaded',
                review_status TEXT NOT NULL DEFAULT 'draft',
                error_message TEXT NOT NULL DEFAULT '',
                pdf_url TEXT NOT NULL DEFAULT ''
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS forms (
                id TEXT PRIMARY KEY,
                receipt_id TEXT NOT NULL UNIQUE REFERENCES receipts(id) ON DELETE RESTRICT,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                form_type TEXT NOT NULL DEFAULT 'Hospitality Expense',
                expense_category TEXT NOT NULL DEFAULT 'Other',
                host TEXT NOT NULL DEFAULT '',
                hosted_persons TEXT NOT NULL DEFAULT '[]',
                occasion TEXT NOT NULL DEFAULT '',
                date_of_hospitality TEXT,
                location_of_hospitality TEXT NOT NULL DEFAULT '',
                invoice_amount REAL,
                tip REAL,
                total_amount REAL,
                merchant TEXT NOT NULL DEFAULT '',
                receipt_number TEXT NOT NULL DEFAULT '',
                form_date TEXT,
                place TEXT NOT NULL DEFAULT '',
                missing_fields TEXT NOT NULL DEFAULT '[]',
                needs_manual_review INTEGER NOT NULL DEFAULT 0,
                ai_confidence TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'draft',
                rejection_reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS combined_documents (
                id TEXT PRIMARY KEY,
                form_id TEXT NOT NULL REFERENCES forms(id) ON DELETE RESTRICT,
                receipt_id TEXT NOT NULL REFERENCES receipts(id) ON DELETE RESTRICT,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                file_path TEXT NOT NULL,
                download_url TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                email_sent INTEGER NOT NULL DEFAULT 0,
                email_sent_at TEXT,
                email_message_id TEXT,
                email_delivery_status TEXT NOT NULL DEFAULT 'pending',
                email_error TEXT,
                user_email TEXT NOT NULL DEFAULT '',
                merchant TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT 'Other',
                host TEXT NOT NULL DEFAULT '',
                occasion TEXT NOT NULL DEFAULT '',
                total_amount REAL,
                currency TEXT NOT NULL DEFAULT 'EUR',
                status TEXT NOT NULL DEFAULT 'processing'
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                action TEXT NOT NULL,
                timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                details TEXT NOT NULL DEFAULT '{}',
                ip_address TEXT NOT NULL DEFAULT '',
                user_agent TEXT NOT NULL DEFAULT '',
                session_id TEXT NOT NULL DEFAULT '',
                read_by TEXT NOT NULL DEFAULT '[]'
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS analytics_cache (
                id TEXT PRIMARY KEY,
                generated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                start_date TEXT NOT NULL DEFAULT '',
                end_date TEXT NOT NULL DEFAULT '',
                data TEXT NOT NULL DEFAULT '{}'
            )
            """,
        ]
        for statement in statements:
            cur.execute(statement)

    def _placeholder(self) -> str:
        return "%s" if self._is_postgres else "?"

    def _collection_config(self, collection_name: str) -> dict[str, Any] | None:
        return TABLE_COLLECTIONS.get(collection_name)

    def _encode_value(self, collection_config: dict[str, Any], field: str, value: Any) -> Any:
        if field in collection_config.get("json", set()):
            if self._is_postgres and Json is not None:
                return Json(value if value is not None else ([] if field == "readBy" else {}))
            return json.dumps(value if value is not None else ([] if field == "readBy" else {}))
        if not self._is_postgres and isinstance(value, bool):
            return 1 if value else 0
        return value

    def _decode_value(self, collection_config: dict[str, Any], field: str, value: Any) -> Any:
        if field in collection_config.get("json", set()):
            if value in (None, ""):
                return [] if field == "readBy" else {}
            if isinstance(value, str):
                try:
                    return json.loads(value)
                except json.JSONDecodeError:
                    return [] if field == "readBy" else {}
            return value
        return value

    def _payload_to_columns(self, collection_config: dict[str, Any], payload: dict) -> dict[str, Any]:
        fields = collection_config["fields"]
        columns: dict[str, Any] = {}
        for field, column in fields.items():
            if field in payload:
                columns[column] = self._encode_value(collection_config, field, payload.get(field))
        return columns

    def _row_to_payload(self, collection_config: dict[str, Any], row: Any, column_names: list[str]) -> dict:
        data = dict(zip(column_names, row))
        reverse_fields = {column: field for field, column in collection_config["fields"].items()}
        payload: dict[str, Any] = {}
        for column, value in data.items():
            field = reverse_fields.get(column)
            if not field:
                continue
            payload[field] = self._decode_value(collection_config, field, value)
        return payload

    def _select_columns(self, collection_config: dict[str, Any]) -> list[str]:
        return [collection_config["id"], *collection_config["fields"].values()]

    def _execute_table_insert(self, cur, collection_config: dict[str, Any], doc_id: str | None, payload: dict) -> str:
        placeholder = self._placeholder()
        table = collection_config["table"]
        id_column = collection_config["id"]
        columns = self._payload_to_columns(collection_config, payload)
        if doc_id is not None:
            columns = {id_column: doc_id, **columns}
        column_names = list(columns.keys())
        placeholders = ", ".join([placeholder] * len(column_names))
        quoted_columns = ", ".join(column_names)
        values = [columns[column] for column in column_names]

        if collection_config.get("auto_id") and doc_id is None:
            sql = f"INSERT INTO {table} ({quoted_columns}) VALUES ({placeholders}) RETURNING {id_column}"
            cur.execute(sql, values)
            row = cur.fetchone()
            return str(row[0])

        update_columns = [column for column in column_names if column != id_column]
        if self._is_postgres:
            if update_columns:
                assignments = ", ".join(f"{column} = EXCLUDED.{column}" for column in update_columns)
                sql = (
                    f"INSERT INTO {table} ({quoted_columns}) VALUES ({placeholders}) "
                    f"ON CONFLICT ({id_column}) DO UPDATE SET {assignments}"
                )
            else:
                sql = (
                    f"INSERT INTO {table} ({quoted_columns}) VALUES ({placeholders}) "
                    f"ON CONFLICT ({id_column}) DO NOTHING"
                )
        else:
            if update_columns:
                assignments = ", ".join(f"{column} = excluded.{column}" for column in update_columns)
                sql = (
                    f"INSERT INTO {table} ({quoted_columns}) VALUES ({placeholders}) "
                    f"ON CONFLICT({id_column}) DO UPDATE SET {assignments}"
                )
            else:
                sql = (
                    f"INSERT INTO {table} ({quoted_columns}) VALUES ({placeholders}) "
                    f"ON CONFLICT({id_column}) DO NOTHING"
                )
        cur.execute(sql, values)
        return str(doc_id)

    def _table_upsert(self, collection_name: str, doc_id: str, payload: dict) -> None:
        collection_config = self._collection_config(collection_name)
        if collection_config is None:
            return
        if self._is_postgres:
            with psycopg.connect(self.database_url) as conn:
                with conn.cursor() as cur:
                    self._execute_table_insert(cur, collection_config, doc_id, payload)
                conn.commit()
            return
        with self._sqlite_lock:
            conn = self._connect_sqlite()
            try:
                cur = conn.cursor()
                self._execute_table_insert(cur, collection_config, doc_id, payload)
                conn.commit()
            finally:
                conn.close()

    def add(self, collection_name: str, payload: dict) -> str:
        collection_config = self._collection_config(collection_name)
        if collection_config is None or not collection_config.get("auto_id"):
            doc_id = str(uuid4())
            self.upsert(collection_name, doc_id, payload)
            return doc_id
        if self._is_postgres:
            with psycopg.connect(self.database_url) as conn:
                with conn.cursor() as cur:
                    doc_id = self._execute_table_insert(cur, collection_config, None, payload)
                conn.commit()
            return doc_id
        with self._sqlite_lock:
            conn = self._connect_sqlite()
            try:
                cur = conn.cursor()
                doc_id = self._execute_table_insert(cur, collection_config, None, payload)
                conn.commit()
                return doc_id
            finally:
                conn.close()

    def upsert(self, collection_name: str, doc_id: str, payload: dict) -> None:
        if self._collection_config(collection_name) is not None:
            self._table_upsert(collection_name, doc_id, payload)
            return
        encoded = json.dumps(payload)
        now = _utc_now_iso()
        if self._is_postgres:
            with psycopg.connect(self.database_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO documents (collection_name, doc_id, payload, updated_at)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (collection_name, doc_id)
                        DO UPDATE SET payload = EXCLUDED.payload, updated_at = EXCLUDED.updated_at
                        """,
                        (collection_name, doc_id, encoded, now),
                    )
                conn.commit()
            return
        with self._sqlite_lock:
            conn = self._connect_sqlite()
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO documents (collection_name, doc_id, payload, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(collection_name, doc_id)
                    DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at
                    """,
                    (collection_name, doc_id, encoded, now),
                )
                conn.commit()
            finally:
                conn.close()

    def get(self, collection_name: str, doc_id: str) -> dict | None:
        collection_config = self._collection_config(collection_name)
        if collection_config is not None:
            columns = self._select_columns(collection_config)
            selected = ", ".join(columns)
            placeholder = self._placeholder()
            sql = (
                f"SELECT {selected} FROM {collection_config['table']} "
                f"WHERE {collection_config['id']} = {placeholder}"
            )
            if self._is_postgres:
                with psycopg.connect(self.database_url) as conn:
                    with conn.cursor() as cur:
                        cur.execute(sql, (doc_id,))
                        row = cur.fetchone()
                        if not row:
                            return None
                        return self._row_to_payload(collection_config, row, columns)
            with self._sqlite_lock:
                conn = self._connect_sqlite()
                try:
                    cur = conn.cursor()
                    cur.execute(sql, (doc_id,))
                    row = cur.fetchone()
                    if not row:
                        return None
                    return self._row_to_payload(collection_config, row, columns)
                finally:
                    conn.close()
        if self._is_postgres:
            with psycopg.connect(self.database_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT payload FROM documents WHERE collection_name = %s AND doc_id = %s",
                        (collection_name, doc_id),
                    )
                    row = cur.fetchone()
                    if not row:
                        return None
                    return json.loads(row[0])
        with self._sqlite_lock:
            conn = self._connect_sqlite()
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT payload FROM documents WHERE collection_name = ? AND doc_id = ?",
                    (collection_name, doc_id),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return json.loads(row[0])
            finally:
                conn.close()

    def list_collection(self, collection_name: str) -> list[tuple[str, dict]]:
        collection_config = self._collection_config(collection_name)
        if collection_config is not None:
            columns = self._select_columns(collection_config)
            selected = ", ".join(columns)
            sql = f"SELECT {selected} FROM {collection_config['table']}"
            if self._is_postgres:
                with psycopg.connect(self.database_url) as conn:
                    with conn.cursor() as cur:
                        cur.execute(sql)
                        rows = cur.fetchall()
                        return [
                            (str(row[0]), self._row_to_payload(collection_config, row, columns))
                            for row in rows
                        ]
            with self._sqlite_lock:
                conn = self._connect_sqlite()
                try:
                    cur = conn.cursor()
                    cur.execute(sql)
                    rows = cur.fetchall()
                    return [
                        (str(row[0]), self._row_to_payload(collection_config, row, columns))
                        for row in rows
                    ]
                finally:
                    conn.close()
        if self._is_postgres:
            with psycopg.connect(self.database_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT doc_id, payload FROM documents WHERE collection_name = %s",
                        (collection_name,),
                    )
                    rows = cur.fetchall()
                    return [(row[0], json.loads(row[1])) for row in rows]
        with self._sqlite_lock:
            conn = self._connect_sqlite()
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT doc_id, payload FROM documents WHERE collection_name = ?",
                    (collection_name,),
                )
                rows = cur.fetchall()
                return [(row[0], json.loads(row[1])) for row in rows]
            finally:
                conn.close()

    def delete(self, collection_name: str, doc_id: str) -> None:
        collection_config = self._collection_config(collection_name)
        if collection_config is not None:
            placeholder = self._placeholder()
            sql = (
                f"DELETE FROM {collection_config['table']} "
                f"WHERE {collection_config['id']} = {placeholder}"
            )
            if self._is_postgres:
                with psycopg.connect(self.database_url) as conn:
                    with conn.cursor() as cur:
                        cur.execute(sql, (doc_id,))
                    conn.commit()
                return
            with self._sqlite_lock:
                conn = self._connect_sqlite()
                try:
                    cur = conn.cursor()
                    cur.execute(sql, (doc_id,))
                    conn.commit()
                finally:
                    conn.close()
            return
        if self._is_postgres:
            with psycopg.connect(self.database_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM documents WHERE collection_name = %s AND doc_id = %s",
                        (collection_name, doc_id),
                    )
                conn.commit()
            return
        with self._sqlite_lock:
            conn = self._connect_sqlite()
            try:
                cur = conn.cursor()
                cur.execute(
                    "DELETE FROM documents WHERE collection_name = ? AND doc_id = ?",
                    (collection_name, doc_id),
                )
                conn.commit()
            finally:
                conn.close()


@dataclass
class _DocumentSnapshot:
    id: str
    _payload: dict | None

    @property
    def exists(self) -> bool:
        return self._payload is not None

    def to_dict(self) -> dict:
        return dict(self._payload or {})


class _DocumentReference:
    def __init__(self, store: "DocumentStore", collection_name: str, doc_id: str):
        self._store = store
        self._collection_name = collection_name
        self.id = doc_id

    def get(self) -> _DocumentSnapshot:
        payload = self._store._backend.get(self._collection_name, self.id)
        return _DocumentSnapshot(id=self.id, _payload=payload)

    def set(self, payload: dict, merge: bool = False) -> None:
        next_payload = dict(payload or {})
        if merge:
            existing = self._store._backend.get(self._collection_name, self.id) or {}
            existing.update(next_payload)
            next_payload = existing
        self._store._backend.upsert(self._collection_name, self.id, next_payload)

    def delete(self) -> None:
        self._store._backend.delete(self._collection_name, self.id)


class _Query:
    def __init__(
        self,
        store: "DocumentStore",
        collection_name: str,
        filters: list[tuple[str, str, Any]] | None = None,
        order: tuple[str, bool] | None = None,
        limit_count: int | None = None,
        start_after: dict | None = None,
    ):
        self._store = store
        self._collection_name = collection_name
        self._filters = filters or []
        self._order = order
        self._limit = limit_count
        self._start_after = start_after

    def where(self, field: str, operator: str, value: Any) -> "_Query":
        return _Query(
            self._store,
            self._collection_name,
            filters=[*self._filters, (field, operator, value)],
            order=self._order,
            limit_count=self._limit,
            start_after=self._start_after,
        )

    def order_by(self, field: str, direction: Any = "ASCENDING") -> "_Query":
        direction_text = str(direction).upper()
        descending = "DESC" in direction_text
        return _Query(
            self._store,
            self._collection_name,
            filters=self._filters,
            order=(field, descending),
            limit_count=self._limit,
            start_after=self._start_after,
        )

    def limit(self, count: int) -> "_Query":
        return _Query(
            self._store,
            self._collection_name,
            filters=self._filters,
            order=self._order,
            limit_count=max(1, int(count)),
            start_after=self._start_after,
        )

    def start_after(self, payload: dict) -> "_Query":
        return _Query(
            self._store,
            self._collection_name,
            filters=self._filters,
            order=self._order,
            limit_count=self._limit,
            start_after=payload or {},
        )

    def _match_filter(self, source: dict, field: str, operator: str, expected: Any) -> bool:
        current = source.get(field)
        if operator == "==":
            return current == expected
        if operator == ">=":
            return current is not None and current >= expected
        if operator == "<=":
            return current is not None and current <= expected
        if operator == ">":
            return current is not None and current > expected
        if operator == "<":
            return current is not None and current < expected
        return False

    def stream(self):
        rows = self._store._backend.list_collection(self._collection_name)
        snapshots = []
        for doc_id, payload in rows:
            if all(self._match_filter(payload, field, op, value) for field, op, value in self._filters):
                snapshots.append(_DocumentSnapshot(id=doc_id, _payload=payload))

        if self._order:
            field, descending = self._order
            snapshots.sort(key=lambda doc: (doc.to_dict().get(field) or ""), reverse=descending)
            if self._start_after and field in self._start_after:
                cursor = self._start_after.get(field)
                filtered = []
                for doc in snapshots:
                    current = doc.to_dict().get(field)
                    if descending:
                        if current < cursor:
                            filtered.append(doc)
                    else:
                        if current > cursor:
                            filtered.append(doc)
                snapshots = filtered
        if self._limit is not None:
            snapshots = snapshots[: self._limit]
        return iter(snapshots)


class _CollectionReference(_Query):
    def __init__(self, store: "DocumentStore", collection_name: str):
        super().__init__(store, collection_name)
        self._store = store
        self._collection_name = collection_name

    def document(self, doc_id: str | None = None) -> _DocumentReference:
        return _DocumentReference(self._store, self._collection_name, doc_id or str(uuid4()))

    def add(self, payload: dict):
        doc_id = self._store._backend.add(self._collection_name, payload)
        ref = self.document(doc_id)
        return ref


class DocumentStore:
    def __init__(self, database_url: str):
        self._backend = _SQLBackend(database_url)

    def collection(self, collection_name: str) -> _CollectionReference:
        return _CollectionReference(self, collection_name)


def init_document_store(database_url: str) -> DocumentStore:
    return DocumentStore(database_url=database_url)

