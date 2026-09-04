# platform/file-storage

> **Prototype / learning module.** Not wired into `app.py` or any agent.
> Built and tested entirely on fictional data (a 1x1 PNG, a stub text doc).
> Same status as `platform/tenancy`.

Tenant-scoped storage for a file's bytes — gated by a `tenancy.TenantScope`
exactly the way `tenancy.ScopedTable` gates row access.

```python
from audit_log import AuditLogStore
from tenancy import TenancyStore
from file_storage import ScopedFileStore, FileNotFound

tenancy = TenancyStore("tenancy.db")
tenancy.create_tenant("acme-books", "Acme Bookkeeping LLC")
scope = tenancy.scope_for("acme-books")

store = ScopedFileStore("./files", audit_log=AuditLogStore("audit.db"))

fid = store.store(scope, png_bytes, filename="receipt.png", content_type="image/png")
data = store.retrieve(scope, fid)                    # FileNotFound under any other scope
store.link_reference(scope, fid, ref_kind="approval_request", ref_value="42")
store.files_for_reference(scope, "approval_request", "42")   # -> [fid]
```

## Guarantees

- **No scope, no access** — `store`, `retrieve`, `get_metadata`,
  `list_by_tenant`, `link_reference` and the reference lookups all call
  `tenancy.require_scope` first; `None` or a bare string raises
  `MissingTenantScope` (the same type `ScopedTable` raises).
- **Cross-tenant isolation** — `retrieve` / `get_metadata` resolve a file
  only via `WHERE file_id = ? AND tenant_id = scope.tenant_id`. A file
  stored under one tenant's scope raises `FileNotFound` under any other
  scope. Unknown file_id and wrong-tenant file_id are **indistinguishable**
  (you can't probe for other tenants' ids).
- **The disk layout is not the boundary.** Files sit at
  `<root>/<tenant_id>/<file_id>`, so a human reading the folder can see who
  owns what — and could `cat` the bytes directly. That's fine: the *module*
  builds the path from `scope.tenant_id` plus a file_id a metadata row has
  already matched to that tenant, so there is no API path that reads another
  tenant's file. The scope check is the access boundary; the folders are
  just tidy. (Both path segments are validated `^[A-Za-z0-9_-]+$` — no
  traversal.)
- **Oversized files are rejected, not truncated** — `store` raises
  `FileTooLarge` (nothing written) above `max_file_bytes`
  (default 10 MiB, constructor-configurable).
- **Unguessable file_ids** — `secrets.token_urlsafe(16)`.
- **Audited** — every `store` (`file_storage.stored`), every `retrieve`
  (`file_storage.retrieved`), every denied retrieve
  (`file_storage.retrieve_denied` — a security signal), and every
  `link_reference` (`file_storage.reference_linked`) is written to the
  injected `AuditLogStore`. **File bytes are never logged** — `sha256` +
  `size_bytes` stand in. Retrieval *is* logged here, unlike
  `ScopedTable`'s unlogged reads: a stored file is evidence for something,
  and "who pulled it / who was refused" is exactly what the trail is for.

Metadata (`files` table): `file_id`, `tenant_id`, `original_filename`,
`content_type`, `size_bytes`, `sha256`, `stored_at`. References
(`file_references` table): `(file_id, tenant_id, ref_kind, ref_value)`.

## Not in this prototype

- object storage (S3/GCS) instead of local disk; encryption at rest;
  content-addressed dedup; deletion / retention; streaming large files
- deriving the scope from a session token; wiring into `platform/approvals`
  so a drafted entry's evidence is stored here automatically
- adding this module to `docs/ARCHITECTURE.md`'s "Identity & Access
  (prototype)" section — a small follow-up

## Development

```bash
cd platform/file-storage && ../../.venv/bin/pytest -v
```

`conftest.py` puts `file_storage/`, `../tenancy`, `../auth`, and
`../audit-log` on `sys.path` — no install step.
