"""Prototype tenant-scoped file storage for the platform chassis.

Learning/prototype component, same status as platform/tenancy: NOT wired
into app.py or any agent. Fictional data only.

Store and retrieve a file's bytes, gated by a tenancy.TenantScope exactly
the way tenancy.ScopedTable gates row access:

    fid   = store.store(scope, content, filename=..., content_type=...)
    bytes = store.retrieve(scope, fid)          # FileNotFound under any other scope
    store.link_reference(scope, fid, ref_kind="approval_request", ref_value="42")

Files land on local disk under <root>/<tenant_id>/<file_id>. That grouping
makes ownership visible to a human reading the folder — but it is NOT the
access boundary. `retrieve` composes the path only after the metadata row
`WHERE file_id = ? AND tenant_id = scope.tenant_id` matches, so another
tenant's bytes are unreachable through this API even though they sit right
there on disk. The scope check is the boundary; the layout is just tidy.

Every store and every retrieve (success or denial) writes to the injected
audit_log.AuditLogStore.

`../tenancy` and `../audit-log` are put on sys.path here.
"""

import sys
from pathlib import Path

_platform = Path(__file__).resolve().parent.parent.parent
for _dep in ("tenancy", "audit-log"):
    _p = str(_platform / _dep)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tenancy import MissingTenantScope  # noqa: E402  (re-exported: one "no scope" type)

from .models import FileMetadata  # noqa: E402
from .store import (  # noqa: E402
    DEFAULT_MAX_FILE_BYTES,
    FileNotFound,
    FileStorageError,
    FileTooLarge,
    ScopedFileStore,
)

__all__ = [
    "FileMetadata",
    "ScopedFileStore",
    "FileStorageError",
    "FileNotFound",
    "FileTooLarge",
    "MissingTenantScope",
    "DEFAULT_MAX_FILE_BYTES",
]
