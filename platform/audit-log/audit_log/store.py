"""Append-only, hash-chained event store backed by local SQLite.

Every event's record_hash is a sha256 of its own fields plus the previous
record's hash, forming a chain. Two independent defenses keep the log
trustworthy:

1. SQLite triggers reject any UPDATE/DELETE against the events table.
2. verify_chain() recomputes every hash and will detect a mismatch even if
   the triggers above were bypassed (e.g. an attacker with direct file
   access dropped them first) — the hash chain is the actual tamper-evidence
   mechanism, the triggers are a cheap first line of defense.
"""

import hashlib
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Union

from .models import AuditEvent, ChainVerificationResult

GENESIS_HASH = "0" * 64

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    agent TEXT NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    inputs TEXT NOT NULL,
    output TEXT NOT NULL,
    model TEXT,
    prompt_hash TEXT,
    approver TEXT,
    approval_status TEXT,
    prev_hash TEXT NOT NULL,
    record_hash TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS events_no_update
BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'audit_log events are append-only: UPDATE is forbidden');
END;

CREATE TRIGGER IF NOT EXISTS events_no_delete
BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'audit_log events are append-only: DELETE is forbidden');
END;
"""

_HASHED_FIELDS = (
    "timestamp",
    "agent",
    "action",
    "actor",
    "inputs",
    "output",
    "model",
    "prompt_hash",
    "approver",
    "approval_status",
    "prev_hash",
)


def _canonical_bytes(fields: dict) -> bytes:
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _record_hash(event: AuditEvent, prev_hash: str) -> str:
    payload = {name: getattr(event, name) for name in _HASHED_FIELDS if name != "prev_hash"}
    payload["prev_hash"] = prev_hash
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


class AuditLogStore:
    def __init__(self, db_path: Union[str, Path]):
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def _last_hash(self) -> str:
        row = self._conn.execute(
            "SELECT record_hash FROM events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else GENESIS_HASH

    def append(self, event: AuditEvent) -> int:
        prev_hash = self._last_hash()
        record_hash = _record_hash(event, prev_hash)

        cursor = self._conn.execute(
            """
            INSERT INTO events (
                timestamp, agent, action, actor, inputs, output,
                model, prompt_hash, approver, approval_status,
                prev_hash, record_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.timestamp,
                event.agent,
                event.action,
                event.actor,
                json.dumps(event.inputs, sort_keys=True),
                json.dumps(event.output, sort_keys=True),
                event.model,
                event.prompt_hash,
                event.approver,
                event.approval_status,
                prev_hash,
                record_hash,
            ),
        )
        self._conn.commit()
        return cursor.lastrowid

    @staticmethod
    def _row_to_event(row: tuple) -> AuditEvent:
        (
            id_, timestamp, agent, action, actor, inputs, output, model,
            prompt_hash, approver, approval_status, prev_hash, record_hash,
        ) = row
        return AuditEvent(
            id=id_,
            timestamp=timestamp,
            agent=agent,
            action=action,
            actor=actor,
            inputs=json.loads(inputs),
            output=json.loads(output),
            model=model,
            prompt_hash=prompt_hash,
            approver=approver,
            approval_status=approval_status,
            prev_hash=prev_hash,
            record_hash=record_hash,
        )

    def get_all(self) -> list[AuditEvent]:
        rows = self._conn.execute(
            "SELECT id, timestamp, agent, action, actor, inputs, output, model, "
            "prompt_hash, approver, approval_status, prev_hash, record_hash "
            "FROM events ORDER BY id ASC"
        ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def verify_chain(self) -> ChainVerificationResult:
        expected_prev = GENESIS_HASH
        for event in self.get_all():
            if event.prev_hash != expected_prev:
                return ChainVerificationResult(
                    ok=False, broken_record_id=event.id, reason="prev_hash mismatch"
                )
            recomputed = _record_hash(event, event.prev_hash)
            if recomputed != event.record_hash:
                return ChainVerificationResult(
                    ok=False, broken_record_id=event.id, reason="record_hash mismatch"
                )
            expected_prev = event.record_hash
        return ChainVerificationResult(ok=True, broken_record_id=None, reason=None)

    def export_evidence_pack(self, path: Union[str, Path]) -> None:
        verification = self.verify_chain()
        payload = {
            "source_db": self.db_path,
            "verification": asdict(verification),
            "events": [asdict(e) for e in self.get_all()],
        }
        Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True, default=str))
