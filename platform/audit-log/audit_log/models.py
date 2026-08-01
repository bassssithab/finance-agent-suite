"""Data model for audit-log events. See docs/ARCHITECTURE.md for the audit-log spec."""

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class AuditEvent:
    """One entry in the hash-chained audit trail.

    `id`, `prev_hash`, and `record_hash` are populated by AuditLogStore on
    append/read — callers only need to set the fields describing the action.
    """

    timestamp: str
    agent: str
    action: str
    actor: str
    inputs: dict[str, Any] = field(default_factory=dict)
    output: Any = None
    model: Optional[str] = None
    prompt_hash: Optional[str] = None
    approver: Optional[str] = None
    approval_status: Optional[str] = None

    id: Optional[int] = None
    prev_hash: Optional[str] = None
    record_hash: Optional[str] = None


@dataclass
class ChainVerificationResult:
    ok: bool
    broken_record_id: Optional[int]
    reason: Optional[str]
