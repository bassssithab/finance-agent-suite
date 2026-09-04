"""SQLite + local-disk store for the prototype tenant-scoped file layer.

Same shape as the other platform stores: construct with a root directory,
schema is created on init, call close() when done. Composes two things:

- tenancy.require_scope / TenantScope — the one shared definition of "a
  valid tenant scope" (ScopedTable uses the same one)
- audit_log.AuditLogStore — injected, REQUIRED; every store and every
  retrieve (success or denial) is appended

Layout:  <root>/index.db
         <root>/<tenant_id>/<file_id>      <- raw bytes, name is the file_id

The per-tenant folders make ownership legible to a human, but they are NOT
the access boundary. retrieve() builds the path from scope.tenant_id + a
file_id that a metadata row has already matched to that same tenant; there
is no code path that reads <root>/<other_tenant>/... under this scope.
"""

import hashlib
import re
import secrets
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

_audit_log_dir = Path(__file__).resolve().parent.parent.parent / "audit-log"
_tenancy_dir = Path(__file__).resolve().parent.parent.parent / "tenancy"
for _d in (_audit_log_dir, _tenancy_dir):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from audit_log import AuditEvent, AuditLogStore  # noqa: E402
from tenancy import require_scope  # noqa: E402

from .models import FileMetadata

_AGENT = "platform/file-storage"
DEFAULT_MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MiB
_FILE_ID_BYTES = 16  # secrets.token_urlsafe(16) -> 22 chars
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    file_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    stored_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS file_references (
    file_id TEXT NOT NULL REFERENCES files(file_id),
    tenant_id TEXT NOT NULL,
    ref_kind TEXT NOT NULL,
    ref_value TEXT NOT NULL,
    linked_at TEXT NOT NULL,
    PRIMARY KEY (file_id, ref_kind, ref_value)
);
"""


class FileStorageError(Exception):
    """Base class for this module's loud failures."""


class FileNotFound(FileStorageError):
    """The file_id does not exist for this scope's tenant.

    Deliberately raised the same way whether the file is unknown or owned by
    another tenant — a caller cannot probe for other tenants' file_ids.
    """


class FileTooLarge(FileStorageError):
    """store() was given more bytes than max_file_bytes. Nothing is written —
    the file is rejected loudly rather than truncated."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _safe_segment(value: str, kind: str) -> str:
    if not isinstance(value, str) or not _SAFE_SEGMENT.match(value):
        raise ValueError(f"unsafe {kind}: {value!r}")
    return value


class ScopedFileStore:
    def __init__(
        self,
        root_dir: Union[str, Path],
        *,
        audit_log: AuditLogStore,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    ):
        self.root = Path(root_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.audit_log = audit_log
        self.max_file_bytes = max_file_bytes
        self._conn = sqlite3.connect(self.root / "index.db")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- audit helper -----------------------------------------------

    def _audit(self, action, actor, *, inputs, output=None, now=None) -> None:
        self.audit_log.append(AuditEvent(
            timestamp=(now or _utcnow()).isoformat(),
            agent=_AGENT,
            action=action,
            actor=actor,
            inputs=inputs,
            output=output,
        ))

    def _path_for(self, tenant_id: str, file_id: str) -> Path:
        return self.root / _safe_segment(tenant_id, "tenant_id") / _safe_segment(file_id, "file_id")

    # -- store -----------------------------------------------------

    def store(
        self,
        scope,
        content: bytes,
        *,
        filename: str,
        content_type: str,
        actor: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> str:
        """Store bytes for the scope's tenant. Returns a new unguessable file_id.

        Raises MissingTenantScope with no valid scope, FileTooLarge if
        `content` exceeds max_file_bytes (nothing is written in that case).
        The bytes are never logged — sha256 + size stand in.
        """
        require_scope(scope)
        if len(content) > self.max_file_bytes:
            raise FileTooLarge(
                f"file is {len(content)} bytes; the limit is {self.max_file_bytes}. "
                "Rejected — not truncated."
            )

        stamp = now or _utcnow()
        file_id = secrets.token_urlsafe(_FILE_ID_BYTES)
        digest = hashlib.sha256(content).hexdigest()

        path = self._path_for(scope.tenant_id, file_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

        self._conn.execute(
            "INSERT INTO files (file_id, tenant_id, original_filename, content_type, "
            "size_bytes, sha256, stored_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (file_id, scope.tenant_id, filename, content_type, len(content), digest,
             stamp.isoformat()),
        )
        self._conn.commit()

        self._audit(
            "file_storage.stored", actor or "unknown",
            inputs={
                "file_id": file_id,
                "tenant_id": scope.tenant_id,
                "filename": filename,
                "content_type": content_type,
                "size_bytes": len(content),
                "sha256": digest,
            },
            now=stamp,
        )
        return file_id

    # -- retrieve (audited — both success and denial) ---------------

    def _owned_row(self, scope, file_id: str):
        return self._conn.execute(
            "SELECT file_id, tenant_id, original_filename, content_type, size_bytes, "
            "sha256, stored_at FROM files WHERE file_id = ? AND tenant_id = ?",
            (file_id, scope.tenant_id),
        ).fetchone()

    def retrieve(
        self,
        scope,
        file_id: str,
        *,
        actor: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> bytes:
        """Return the bytes of a file the scope's tenant owns.

        Raises MissingTenantScope with no valid scope. Raises FileNotFound if
        `file_id` is unknown OR belongs to another tenant — the two are
        indistinguishable on purpose. Both outcomes are audited.
        """
        require_scope(scope)
        try:
            _safe_segment(file_id, "file_id")
        except ValueError:
            self._audit(
                "file_storage.retrieve_denied", actor or "unknown",
                inputs={"file_id": str(file_id)[:64], "requested_by_tenant": scope.tenant_id,
                        "reason": "malformed_file_id"},
                now=now,
            )
            raise FileNotFound(f"no file {file_id!r} for this tenant")

        row = self._owned_row(scope, file_id)
        if row is None:
            self._audit(
                "file_storage.retrieve_denied", actor or "unknown",
                inputs={"file_id": file_id, "requested_by_tenant": scope.tenant_id,
                        "reason": "not_owned_or_missing"},
                now=now,
            )
            raise FileNotFound(f"no file {file_id!r} for this tenant")

        content = self._path_for(scope.tenant_id, file_id).read_bytes()
        self._audit(
            "file_storage.retrieved", actor or "unknown",
            inputs={"file_id": file_id, "tenant_id": scope.tenant_id},
            output={"size_bytes": len(content), "sha256": row[5]},
            now=now,
        )
        return content

    # -- metadata / listing (not audited — no bytes leave) ----------

    def get_metadata(self, scope, file_id: str) -> Optional[FileMetadata]:
        """The file's metadata if the scope's tenant owns it, else None
        (mirrors tenancy.ScopedTable.get)."""
        require_scope(scope)
        try:
            _safe_segment(file_id, "file_id")
        except ValueError:
            return None
        row = self._owned_row(scope, file_id)
        return self._row_to_meta(row) if row is not None else None

    def list_by_tenant(self, scope) -> list[FileMetadata]:
        """Every file the scope's tenant owns."""
        require_scope(scope)
        rows = self._conn.execute(
            "SELECT file_id, tenant_id, original_filename, content_type, size_bytes, "
            "sha256, stored_at FROM files WHERE tenant_id = ? ORDER BY stored_at, file_id",
            (scope.tenant_id,),
        ).fetchall()
        return [self._row_to_meta(r) for r in rows]

    @staticmethod
    def _row_to_meta(row) -> FileMetadata:
        return FileMetadata(
            file_id=row[0], tenant_id=row[1], original_filename=row[2],
            content_type=row[3], size_bytes=row[4], sha256=row[5], stored_at=row[6],
        )

    # -- external references --------------------------------------

    def link_reference(
        self,
        scope,
        file_id: str,
        *,
        ref_kind: str,
        ref_value: str,
        actor: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> None:
        """Associate a stored file with an external identifier (e.g. an
        approvals request id) so it stays linked to whatever it is evidence
        for. Raises FileNotFound if the scope's tenant does not own file_id."""
        require_scope(scope)
        if self._owned_row(scope, file_id) is None:
            raise FileNotFound(f"no file {file_id!r} for this tenant")

        stamp = now or _utcnow()
        self._conn.execute(
            "INSERT OR IGNORE INTO file_references (file_id, tenant_id, ref_kind, "
            "ref_value, linked_at) VALUES (?, ?, ?, ?, ?)",
            (file_id, scope.tenant_id, ref_kind, ref_value, stamp.isoformat()),
        )
        self._conn.commit()

        self._audit(
            "file_storage.reference_linked", actor or "unknown",
            inputs={"file_id": file_id, "tenant_id": scope.tenant_id,
                    "ref_kind": ref_kind, "ref_value": ref_value},
            now=stamp,
        )

    def files_for_reference(self, scope, ref_kind: str, ref_value: str) -> list[str]:
        """file_ids linked to (ref_kind, ref_value) for the scope's tenant."""
        require_scope(scope)
        rows = self._conn.execute(
            "SELECT file_id FROM file_references WHERE tenant_id = ? AND ref_kind = ? "
            "AND ref_value = ? ORDER BY linked_at, file_id",
            (scope.tenant_id, ref_kind, ref_value),
        ).fetchall()
        return [r[0] for r in rows]

    def references_for_file(self, scope, file_id: str) -> list[tuple[str, str]]:
        """(ref_kind, ref_value) pairs linked to file_id, if the scope's tenant
        owns it (else an empty list)."""
        require_scope(scope)
        if self._owned_row(scope, file_id) is None:
            return []
        rows = self._conn.execute(
            "SELECT ref_kind, ref_value FROM file_references WHERE file_id = ? AND "
            "tenant_id = ? ORDER BY linked_at, ref_kind, ref_value",
            (file_id, scope.tenant_id),
        ).fetchall()
        return [(r[0], r[1]) for r in rows]
