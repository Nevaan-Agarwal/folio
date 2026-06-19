"""Firestore-like document store backed by SQL databases."""

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
except Exception:  # pragma: no cover - optional dependency at runtime
    psycopg = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_sqlite_url(database_url: str) -> str:
    if database_url.startswith("sqlite:///"):
        return database_url.removeprefix("sqlite:///")
    if database_url.startswith("sqlite://"):
        return database_url.removeprefix("sqlite://")
    return database_url


class _SQLBackend:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self._sqlite_lock = threading.Lock()
        self._is_postgres = database_url.startswith("postgresql://") or database_url.startswith("postgres://")
        self._sqlite_path = ""
        if self._is_postgres and psycopg is None:
            # Keep local/dev/test environments working even when postgres driver
            # is unavailable by gracefully falling back to SQLite.
            self._is_postgres = False
            self.database_url = "sqlite:///folio.db"
        if not self._is_postgres:
            self._sqlite_path = _normalize_sqlite_url(self.database_url) or "folio.db"
            Path(self._sqlite_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect_sqlite(self):
        return sqlite3.connect(self._sqlite_path, check_same_thread=False)

    def _init_schema(self) -> None:
        sql = (
            "CREATE TABLE IF NOT EXISTS documents ("
            "collection_name TEXT NOT NULL,"
            "doc_id TEXT NOT NULL,"
            "payload TEXT NOT NULL,"
            "updated_at TEXT NOT NULL,"
            "PRIMARY KEY (collection_name, doc_id)"
            ");"
        )
        idx = "CREATE INDEX IF NOT EXISTS idx_documents_collection ON documents(collection_name);"
        if self._is_postgres:
            with psycopg.connect(self.database_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    cur.execute(idx)
                conn.commit()
            return
        with self._sqlite_lock:
            conn = self._connect_sqlite()
            try:
                cur = conn.cursor()
                cur.execute(sql)
                cur.execute(idx)
                conn.commit()
            finally:
                conn.close()

    def upsert(self, collection_name: str, doc_id: str, payload: dict) -> None:
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
        ref = self.document()
        ref.set(payload)
        return ref


class DocumentStore:
    def __init__(self, database_url: str):
        self._backend = _SQLBackend(database_url)

    def collection(self, collection_name: str) -> _CollectionReference:
        return _CollectionReference(self, collection_name)


def init_document_store(database_url: str) -> DocumentStore:
    return DocumentStore(database_url=database_url)

