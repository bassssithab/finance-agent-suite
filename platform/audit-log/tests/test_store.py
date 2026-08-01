import sqlite3

import pytest

from audit_log import AuditEvent, AuditLogStore
from audit_log.store import GENESIS_HASH


def make_event(action="draft_journal_entry", **overrides):
    fields = dict(
        timestamp="2026-08-01T12:00:00Z",
        agent="reconciliation-agent",
        action=action,
        actor="system",
        inputs={"bank_statement_id": "stmt-1", "gl_account": "1000"},
        output={"match_count": 3},
        model="claude-sonnet-5",
        prompt_hash="deadbeef",
        approver=None,
        approval_status="draft",
    )
    fields.update(overrides)
    return AuditEvent(**fields)


@pytest.fixture
def store(tmp_path):
    s = AuditLogStore(tmp_path / "audit.db")
    yield s
    s.close()


def test_append_and_read_round_trip(store):
    store.append(make_event(action="draft"))
    store.append(make_event(action="approve", approver="alice", approval_status="approved"))

    events = store.get_all()

    assert [e.action for e in events] == ["draft", "approve"]
    assert events[0].inputs == {"bank_statement_id": "stmt-1", "gl_account": "1000"}
    assert events[1].approver == "alice"
    assert events[1].approval_status == "approved"


def test_hash_chain_links_records_in_order(store):
    for i in range(4):
        store.append(make_event(action=f"action-{i}"))

    events = store.get_all()

    assert events[0].prev_hash == GENESIS_HASH
    for prev, curr in zip(events, events[1:]):
        assert curr.prev_hash == prev.record_hash

    result = store.verify_chain()
    assert result.ok is True
    assert result.broken_record_id is None


def test_verify_chain_detects_tampering(store, tmp_path):
    store.append(make_event(action="draft"))
    store.append(make_event(action="approve", approver="alice"))
    store.append(make_event(action="post"))

    # Simulate an attacker with direct file access: drop the append-only
    # triggers, then mutate a field on a row already written to disk.
    raw = sqlite3.connect(tmp_path / "audit.db")
    raw.execute("DROP TRIGGER events_no_update")
    raw.execute("UPDATE events SET output = ? WHERE action = 'approve'", ('{"tampered": true}',))
    raw.commit()
    raw.close()

    result = store.verify_chain()

    assert result.ok is False
    tampered_id = [e.id for e in store.get_all() if e.action == "approve"][0]
    assert result.broken_record_id == tampered_id


def test_events_table_rejects_update_and_delete(store, tmp_path):
    store.append(make_event())
    raw = sqlite3.connect(tmp_path / "audit.db")

    with pytest.raises(sqlite3.IntegrityError):
        raw.execute("UPDATE events SET action = 'rewritten' WHERE id = 1")

    with pytest.raises(sqlite3.IntegrityError):
        raw.execute("DELETE FROM events WHERE id = 1")

    raw.close()


def test_export_evidence_pack(store, tmp_path):
    store.append(make_event(action="draft"))
    store.append(make_event(action="approve", approver="alice", approval_status="approved"))

    pack_path = tmp_path / "evidence_pack.json"
    store.export_evidence_pack(pack_path)

    import json

    payload = json.loads(pack_path.read_text())

    assert payload["verification"]["ok"] is True
    assert len(payload["events"]) == 2
    assert payload["events"][1]["approver"] == "alice"
