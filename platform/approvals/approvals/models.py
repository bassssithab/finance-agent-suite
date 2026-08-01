"""Data model for the approvals queue. See docs/ARCHITECTURE.md for the spec."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Role(str, Enum):
    PREPARER = "preparer"
    REVIEWER = "reviewer"
    APPROVER = "approver"


class Decision(str, Enum):
    APPROVE = "approve"
    EDIT = "edit"
    REJECT = "reject"


@dataclass
class ApprovalRequest:
    agent: str
    action: str
    payload: dict[str, Any]
    preparer: str
    status: str
    current_stage: Optional[str]
    created_at: str
    updated_at: str
    id: Optional[int] = None


@dataclass
class ApprovalDecisionRecord:
    request_id: int
    role: str
    actor: str
    decision: str
    comment: Optional[str]
    payload_after: Optional[dict[str, Any]]
    timestamp: str
    id: Optional[int] = None
