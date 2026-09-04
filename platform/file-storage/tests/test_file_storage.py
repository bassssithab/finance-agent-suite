import json
from dataclasses import asdict

import pytest

from audit_log import AuditLogStore
from tenancy import MissingTenantScope, TenancyStore
from file_storage import (
    FileMetadata,
    FileNotFound,
    FileTooLarge,
    ScopedFileStore,
)

from fixtures import FICTIONAL_TENANTS, SAMPLE_DOC, TINY_PNG


@pytest.fixture
def audit_log(tmp_path):
    log = AuditLogStore(tmp_path / "audit.db")
    yield log
    log.close()


@pytest.fixture(autouse=True)
def _audit_chain_stays_intact(audit_log):
    yield
    assert audit_log.verify_chain().ok is True


@pytest.fixture
def tenancy_store(tmp_path):
    s = TenancyStore(tmp_path / "tenancy.db")
    for tenant_id, display_name in FICTIONAL_TENANTS:
        s.create_tenant(tenant_id, display_name)
    yield s
    s.close()


@pytest.fixture
def scope_a(tenancy_store):
    return tenancy_store.scope_for("acme-books")


@pytest.fixture
def scope_b(tenancy_store):
    return tenancy_store.scope_for("globex-finance")


@pytest.fixture
def store(tmp_path, audit_log):
    s = ScopedFileStore(tmp_path / "filestore", audit_log=audit_log)
    yield s
    s.close()


def _actions(audit_log):
    return [e.action for e in audit_log.get_all()]


def _serialized(audit_log):
    return json.dumps([asdict(e) for e in audit_log.get_all()], default=str)


# ---------------------------------------------------------------------------
# 1. store then retrieve round-trips
# ---------------------------------------------------------------------------

def test_store_then_retrieve_round_trips(store, scope_a, audit_log):
    fid = store.store(
        scope_a, TINY_PNG, filename="receipt.png", content_type="image/png",
        actor="dana.acme",
    )

    assert isinstance(fid, str) and fid
    assert store.retrieve(scope_a, fid) == TINY_PNG

    meta = store.get_metadata(scope_a, fid)
    assert isinstance(meta, FileMetadata)
    assert meta.original_filename == "receipt.png"
    assert meta.content_type == "image/png"
    assert meta.size_bytes == len(TINY_PNG)
    assert meta.tenant_id == "acme-books"

    assert _actions(audit_log) == ["file_storage.stored", "file_storage.retrieved"]


# ---------------------------------------------------------------------------
# 2. THE IMPORTANT ONE — a file stored under tenant A's scope is never
#    retrievable under tenant B's scope (mirrors the tenancy isolation test)
# ---------------------------------------------------------------------------

def test_a_file_is_never_retrievable_under_another_tenants_scope(store, scope_a, scope_b, tmp_path, audit_log):
    fid = store.store(
        scope_a, b"ACME CONFIDENTIAL PAYROLL FIGURE",
        filename="secret.txt", content_type="text/plain",
    )

    # Tenant B, holding the exact file_id, cannot retrieve it.
    with pytest.raises(FileNotFound):
        store.retrieve(scope_b, fid)

    # Nor see its metadata, nor find it in a listing.
    assert store.get_metadata(scope_b, fid) is None
    assert fid not in [m.file_id for m in store.list_by_tenant(scope_b)]

    # The bytes ARE physically on disk under acme-books/ — the isolation is
    # the scope check, not the file being absent.
    on_disk = tmp_path / "filestore" / "acme-books" / fid
    assert on_disk.read_bytes() == b"ACME CONFIDENTIAL PAYROLL FIGURE"

    # Tenant A still gets its own file back.
    assert store.retrieve(scope_a, fid) == b"ACME CONFIDENTIAL PAYROLL FIGURE"

    # The denied attempt was audited as a security signal.
    denied = [e for e in audit_log.get_all() if e.action == "file_storage.retrieve_denied"]
    assert len(denied) == 1
    assert denied[0].inputs["requested_by_tenant"] == "globex-finance"

    # And the mirror direction: A cannot reach a file B stored.
    fid_b = store.store(scope_b, b"GLOBEX SECRET", filename="g.txt", content_type="text/plain")
    with pytest.raises(FileNotFound):
        store.retrieve(scope_a, fid_b)


def test_unknown_file_id_and_wrong_tenant_are_indistinguishable(store, scope_a, scope_b):
    fid = store.store(scope_a, SAMPLE_DOC, filename="d.txt", content_type="text/plain")

    with pytest.raises(FileNotFound):
        store.retrieve(scope_b, fid)          # exists, wrong tenant
    with pytest.raises(FileNotFound):
        store.retrieve(scope_a, "Zt0nExiStEnT0000000000")  # well-formed, unknown


# ---------------------------------------------------------------------------
# 3. retrieval with no scope fails loudly
# ---------------------------------------------------------------------------

def test_retrieve_with_no_scope_fails_loudly(store, scope_a, tmp_path):
    fid = store.store(scope_a, SAMPLE_DOC, filename="d.txt", content_type="text/plain")

    with pytest.raises(MissingTenantScope):
        store.retrieve(None, fid)
    with pytest.raises(MissingTenantScope):
        store.retrieve("acme-books", fid)          # a bare string is not a scope
    with pytest.raises(MissingTenantScope):
        store.get_metadata(None, fid)
    with pytest.raises(MissingTenantScope):
        store.list_by_tenant(None)

    # store() with no scope raises and writes nothing to disk.
    with pytest.raises(MissingTenantScope):
        store.store(None, b"x", filename="x", content_type="text/plain")
    tenant_dirs = [p.name for p in (tmp_path / "filestore").iterdir() if p.is_dir()]
    assert tenant_dirs == ["acme-books"]  # only the one legitimate store above


# ---------------------------------------------------------------------------
# 4. the file-to-reference association
# ---------------------------------------------------------------------------

def test_file_to_reference_association(store, scope_a, scope_b, audit_log):
    fid1 = store.store(scope_a, TINY_PNG, filename="a.png", content_type="image/png")
    fid2 = store.store(scope_a, SAMPLE_DOC, filename="b.txt", content_type="text/plain")

    store.link_reference(scope_a, fid1, ref_kind="approval_request", ref_value="42")
    store.link_reference(scope_a, fid2, ref_kind="approval_request", ref_value="42")
    store.link_reference(scope_a, fid1, ref_kind="close_run", ref_value="2026-08")

    assert sorted(store.files_for_reference(scope_a, "approval_request", "42")) == sorted([fid1, fid2])
    assert store.references_for_file(scope_a, fid1) == [
        ("approval_request", "42"), ("close_run", "2026-08"),
    ]

    # References are tenant-scoped too.
    assert store.files_for_reference(scope_b, "approval_request", "42") == []

    assert "file_storage.reference_linked" in _actions(audit_log)


def test_link_reference_for_a_file_another_tenant_owns_fails(store, scope_a, scope_b):
    fid = store.store(scope_a, SAMPLE_DOC, filename="d.txt", content_type="text/plain")
    with pytest.raises(FileNotFound):
        store.link_reference(scope_b, fid, ref_kind="approval_request", ref_value="1")


# ---------------------------------------------------------------------------
# supporting
# ---------------------------------------------------------------------------

def test_oversized_file_is_rejected_loudly_not_truncated(tmp_path, audit_log, scope_a):
    small = ScopedFileStore(tmp_path / "fs", audit_log=audit_log, max_file_bytes=100)

    with pytest.raises(FileTooLarge):
        small.store(scope_a, b"x" * 101, filename="big.bin", content_type="application/octet-stream")

    assert small.list_by_tenant(scope_a) == []       # nothing recorded
    assert not (tmp_path / "fs" / "acme-books").exists()  # nothing on disk
    small.close()


def test_file_bytes_are_never_in_the_audit_log(store, scope_a, audit_log):
    marker = b"TOP-SECRET-MARKER-STRING-9271"
    fid = store.store(scope_a, marker + b" padding", filename="s.txt", content_type="text/plain")
    store.retrieve(scope_a, fid)

    blob = _serialized(audit_log)
    assert "TOP-SECRET-MARKER-STRING-9271" not in blob
    assert store.get_metadata(scope_a, fid).sha256 in blob   # the hash stands in


def test_disk_layout_groups_files_by_tenant(store, scope_a, scope_b, tmp_path):
    store.store(scope_a, TINY_PNG, filename="a.png", content_type="image/png")
    store.store(scope_b, SAMPLE_DOC, filename="b.txt", content_type="text/plain")

    root = tmp_path / "filestore"
    assert (root / "acme-books").is_dir()
    assert (root / "globex-finance").is_dir()
    assert len(list((root / "acme-books").iterdir())) == 1
    assert len(list((root / "globex-finance").iterdir())) == 1


def test_path_traversal_file_id_is_rejected(store, scope_a):
    with pytest.raises(FileNotFound):
        store.retrieve(scope_a, "../index.db")
    assert store.get_metadata(scope_a, "../index.db") is None


def test_two_stores_get_distinct_unguessable_ids(store, scope_a):
    a = store.store(scope_a, b"one", filename="1", content_type="text/plain")
    b = store.store(scope_a, b"two", filename="2", content_type="text/plain")
    assert a != b
    assert len(a) >= 20 and len(b) >= 20
