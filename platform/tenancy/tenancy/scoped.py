"""The tenant-scoped data-access pattern — a small, self-contained demo.

This is NOT an agent integration. It is the smallest thing that shows how a
data lookup can be made to *always* filter by tenant_id, so that returning
another tenant's rows is not an operation you can express by accident.

The rules that make it hard to get wrong:

1. Every public method REQUIRES a TenantScope as its first argument.
2. The method composes `WHERE tenant_id = ?` itself, from the scope — the
   caller never writes the filter and cannot omit it.
3. `insert` takes tenant_id ONLY from the scope. Passing a `tenant_id` in
   the row fields is a loud error, not a silent override.
4. There is no public method that returns rows without a scope, and the raw
   connection is not exposed.

In a real deployment this is the hand-rolled stand-in for Postgres
row-level security (see docs/ARCHITECTURE.md, "Multi-tenant, row-level
security").
"""

import re
import sqlite3
from typing import Any, Optional

from .models import TenantScope

__all__ = ["MissingTenantScope", "ScopedTable"]

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class MissingTenantScope(Exception):
    """Raised when a scoped query is attempted without a valid TenantScope."""


def _require_scope(scope: Any) -> TenantScope:
    if scope is None:
        raise MissingTenantScope(
            "no tenant scope supplied; every scoped query must name a tenant"
        )
    if not isinstance(scope, TenantScope):
        raise MissingTenantScope(
            f"expected a TenantScope, got {type(scope).__name__}; "
            "pass store.scope_for(...) / scope_for_user(...), not a bare id"
        )
    return scope


def _check_identifier(name: str, kind: str) -> str:
    if not _IDENTIFIER.match(name):
        raise ValueError(f"unsafe {kind} name: {name!r}")
    return name


class ScopedTable:
    """Wraps one SQLite table whose rows carry a `tenant_id` column.

    The table must already exist with an integer `id` primary key, a
    `tenant_id TEXT NOT NULL` column, and any number of other columns.
    """

    def __init__(self, conn: sqlite3.Connection, table: str):
        self._conn = conn
        self._table = _check_identifier(table, "table")

    def insert(self, scope: Any, **fields: Any) -> int:
        """Insert a row, stamping tenant_id from the scope. Returns the new id."""
        _require_scope(scope)
        if "tenant_id" in fields:
            raise ValueError(
                "do not pass tenant_id to insert(); it is taken from the scope. "
                f"(scope tenant_id={scope.tenant_id!r}, "
                f"attempted tenant_id={fields['tenant_id']!r})"
            )
        columns = ["tenant_id"] + [_check_identifier(k, "column") for k in fields]
        placeholders = ", ".join("?" for _ in columns)
        values = [scope.tenant_id] + list(fields.values())
        cursor = self._conn.execute(
            f"INSERT INTO {self._table} ({', '.join(columns)}) VALUES ({placeholders})",
            values,
        )
        self._conn.commit()
        return cursor.lastrowid

    def all(self, scope: Any) -> list[dict]:
        """Return every row belonging to the scope's tenant, as dicts."""
        _require_scope(scope)
        cursor = self._conn.execute(
            f"SELECT * FROM {self._table} WHERE tenant_id = ?",
            (scope.tenant_id,),
        )
        return [self._row_to_dict(cursor, row) for row in cursor.fetchall()]

    def get(self, scope: Any, row_id: int) -> Optional[dict]:
        """Return one row by id, but only if it belongs to the scope's tenant."""
        _require_scope(scope)
        cursor = self._conn.execute(
            f"SELECT * FROM {self._table} WHERE id = ? AND tenant_id = ?",
            (row_id, scope.tenant_id),
        )
        row = cursor.fetchone()
        return self._row_to_dict(cursor, row) if row is not None else None

    @staticmethod
    def _row_to_dict(cursor: sqlite3.Cursor, row: tuple) -> dict:
        return {col[0]: value for col, value in zip(cursor.description, row)}
