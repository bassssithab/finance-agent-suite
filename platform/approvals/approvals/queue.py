"""Human-in-the-loop approval queue: preparer -> reviewer -> approver.

Two storage layers, two different jobs:
- audit_log.AuditLogStore is the tamper-evident system of record.
- This module's own SQLite tables are mutable *operational* state (what's
  pending in whose queue right now) plus an insert-only decision history for
  convenient querying. Every state change here is also mirrored into
  audit_log as the authoritative record.
"""

import json
import sqlite3
from pathlib import Path
from typing import Optional, Union

from audit_log import AuditEvent, AuditLogStore

from .models import ApprovalDecisionRecord, ApprovalRequest, Decision, Role

_DEFAULT_ROUTING = (Role.REVIEWER, Role.APPROVER)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS approval_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    action TEXT NOT NULL,
    payload TEXT NOT NULL,
    preparer TEXT NOT NULL,
    status TEXT NOT NULL,
    current_stage TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approval_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL REFERENCES approval_requests(id),
    role TEXT NOT NULL,
    actor TEXT NOT NULL,
    decision TEXT NOT NULL,
    comment TEXT,
    payload_after TEXT,
    timestamp TEXT NOT NULL
);
"""


class WrongStageError(Exception):
    """Raised when a decision's role doesn't match the request's current stage,
    or the request is already terminal (approved/rejected)."""


class SegregationOfDutyError(Exception):
    """Raised when an actor tries to act more than once on the same request
    (e.g. the preparer also reviewing, or the reviewer also approving)."""


class RequestNotFound(Exception):
    pass


class ApprovalQueue:
    def __init__(
        self,
        db_path: Union[str, Path],
        audit_log: AuditLogStore,
        routing: tuple = _DEFAULT_ROUTING,
    ):
        self.db_path = str(db_path)
        self.audit_log = audit_log
        self.routing = routing
        self._conn = sqlite3.connect(self.db_path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def submit(
        self,
        agent: str,
        action: str,
        payload: dict,
        preparer: str,
        timestamp: str,
    ) -> ApprovalRequest:
        status = "pending"
        current_stage = self.routing[0].value
        cursor = self._conn.execute(
            """
            INSERT INTO approval_requests (
                agent, action, payload, preparer, status, current_stage,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                agent, action, json.dumps(payload, sort_keys=True), preparer,
                status, current_stage, timestamp, timestamp,
            ),
        )
        self._conn.commit()
        request_id = cursor.lastrowid

        self.audit_log.append(AuditEvent(
            timestamp=timestamp,
            agent=agent,
            action=f"approval_submitted:{action}",
            actor=preparer,
            inputs={"request_id": request_id, "payload": payload},
            output={"status": status, "current_stage": current_stage},
            approver=None,
            approval_status=status,
        ))

        return self.get(request_id)

    def get(self, request_id: int) -> ApprovalRequest:
        row = self._conn.execute(
            "SELECT id, agent, action, payload, preparer, status, current_stage, "
            "created_at, updated_at FROM approval_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        if row is None:
            raise RequestNotFound(f"no approval request with id {request_id}")
        return self._row_to_request(row)

    @staticmethod
    def _row_to_request(row: tuple) -> ApprovalRequest:
        (id_, agent, action, payload, preparer, status, current_stage,
         created_at, updated_at) = row
        return ApprovalRequest(
            id=id_,
            agent=agent,
            action=action,
            payload=json.loads(payload),
            preparer=preparer,
            status=status,
            current_stage=current_stage,
            created_at=created_at,
            updated_at=updated_at,
        )

    def list_pending(self, role: Optional[Role] = None) -> list:
        if role is None:
            rows = self._conn.execute(
                "SELECT id, agent, action, payload, preparer, status, current_stage, "
                "created_at, updated_at FROM approval_requests WHERE status = 'pending' "
                "ORDER BY id ASC"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id, agent, action, payload, preparer, status, current_stage, "
                "created_at, updated_at FROM approval_requests "
                "WHERE status = 'pending' AND current_stage = ? ORDER BY id ASC",
                (role.value,),
            ).fetchall()
        return [self._row_to_request(row) for row in rows]

    def decisions_for(self, request_id: int) -> list:
        rows = self._conn.execute(
            "SELECT id, request_id, role, actor, decision, comment, payload_after, "
            "timestamp FROM approval_decisions WHERE request_id = ? ORDER BY id ASC",
            (request_id,),
        ).fetchall()
        result = []
        for (id_, request_id_, role, actor, decision, comment, payload_after, timestamp) in rows:
            result.append(ApprovalDecisionRecord(
                id=id_,
                request_id=request_id_,
                role=role,
                actor=actor,
                decision=decision,
                comment=comment,
                payload_after=json.loads(payload_after) if payload_after is not None else None,
                timestamp=timestamp,
            ))
        return result

    def _prior_actors(self, request: ApprovalRequest) -> set:
        actors = {request.preparer}
        for record in self.decisions_for(request.id):
            actors.add(record.actor)
        return actors

    def decide(
        self,
        request_id: int,
        actor: str,
        role: Role,
        decision: Decision,
        timestamp: str,
        comment: Optional[str] = None,
        edited_payload: Optional[dict] = None,
    ) -> ApprovalRequest:
        request = self.get(request_id)

        if request.status != "pending":
            raise WrongStageError(
                f"request {request_id} is already {request.status}, no further decisions allowed"
            )
        if request.current_stage != role.value:
            raise WrongStageError(
                f"request {request_id} is awaiting {request.current_stage}, not {role.value}"
            )
        if actor in self._prior_actors(request):
            raise SegregationOfDutyError(
                f"actor {actor!r} already acted on request {request_id}; "
                "the same person cannot act twice in the approval chain"
            )

        if decision == Decision.REJECT:
            new_status = "rejected"
            new_stage = None
            new_payload = request.payload
        else:
            if decision == Decision.EDIT:
                if edited_payload is None:
                    raise ValueError("edited_payload is required when decision is EDIT")
                new_payload = edited_payload
            else:
                new_payload = request.payload
            stage_index = self.routing.index(role)
            if stage_index + 1 < len(self.routing):
                new_status = "pending"
                new_stage = self.routing[stage_index + 1].value
            else:
                new_status = "approved"
                new_stage = None

        self._conn.execute(
            "UPDATE approval_requests SET payload = ?, status = ?, current_stage = ?, "
            "updated_at = ? WHERE id = ?",
            (json.dumps(new_payload, sort_keys=True), new_status, new_stage, timestamp, request_id),
        )
        self._conn.execute(
            """
            INSERT INTO approval_decisions (
                request_id, role, actor, decision, comment, payload_after, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id, role.value, actor, decision.value, comment,
                json.dumps(edited_payload, sort_keys=True) if decision == Decision.EDIT else None,
                timestamp,
            ),
        )
        self._conn.commit()

        self.audit_log.append(AuditEvent(
            timestamp=timestamp,
            agent=request.agent,
            action=f"approval_{role.value}_{decision.value}:{request.action}",
            actor=actor,
            inputs={
                "request_id": request_id,
                "role": role.value,
                "decision": decision.value,
                "comment": comment,
            },
            output={"status": new_status, "current_stage": new_stage},
            approver=actor if role == Role.APPROVER else None,
            approval_status=new_status,
        ))

        return self.get(request_id)
