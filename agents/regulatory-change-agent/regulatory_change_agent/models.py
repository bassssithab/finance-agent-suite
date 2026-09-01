"""Data model for the regulatory-change-agent triage workflow: the triage
policy, one control's relevance score, the drafted impact-assessment narrative,
and the triage report that goes to approvals.

A `TriageReport` is a FIRST-PASS TRIAGE, not a compliance determination —
CLAUDE.md rule #2 requires it to go through `platform/approvals`, and the
report itself never concludes the company is or isn't compliant; that is a
qualified legal/compliance professional's call. This module only shapes the
data and its audit/approval payloads; the deterministic keyword/category
relevance match lives in `triage.py` (plain code, rule #4), the narrative
drafting in `narrate.py` (the only LLM call), orchestration in `runner.py`.
"""

from dataclasses import dataclass, field
from typing import Optional

COVERAGE_VERDICTS = ("likely_gap", "weak_coverage", "apparent_coverage")


# ---------------------------------------------------------------------------
# Triage policy (consumed by triage.py) — configurable per run
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TriagePolicy:
    """The tuning inputs to the deterministic relevance match.

    `min_keyword_overlap` is the number of shared key terms (after stopword
    removal and light stemming) at or above which a control is treated as
    *appearing* relevant. `strong_overlap` is the score at or above which a
    single control is treated as a solid apparent match on its own. When the
    best relevant control is below `strong_overlap`, or only one control is
    relevant at all, the verdict is `weak_coverage` rather than
    `apparent_coverage`. `max_controls_surfaced` caps how many relevant controls
    are carried into the report and the narrative. `extra_stopwords` extends the
    builtin stopword set for a noisy control vocabulary.
    """

    min_keyword_overlap: int = 2
    strong_overlap: int = 4
    max_controls_surfaced: int = 8
    extra_stopwords: tuple = ()

    def to_dict(self) -> dict:
        return {
            "min_keyword_overlap": self.min_keyword_overlap,
            "strong_overlap": self.strong_overlap,
            "max_controls_surfaced": self.max_controls_surfaced,
            "extra_stopwords": list(self.extra_stopwords),
        }


# ---------------------------------------------------------------------------
# One control's relevance (produced by triage.py) — pure code, no LLM
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ControlRelevance:
    control_id: str
    description: str
    category: str
    score: int                   # shared key terms (+ category-match bonus)
    matched_terms: list          # the shared key terms, sorted
    category_match: bool          # a requirement term equals a term in the control's category
    relevant: bool                # score >= policy.min_keyword_overlap

    def to_dict(self) -> dict:
        return {
            "control_id": self.control_id,
            "description": self.description,
            "category": self.category,
            "score": self.score,
            "matched_terms": list(self.matched_terms),
            "category_match": self.category_match,
            "relevant": self.relevant,
        }


# ---------------------------------------------------------------------------
# The deterministic triage result (triage.py output)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TriageResult:
    relevances: list             # list[ControlRelevance], every control, ranked
    relevant_controls: list      # the surfaced subset (relevant, capped, ranked)
    coverage_verdict: str        # one of COVERAGE_VERDICTS
    gap_flagged: bool            # True for likely_gap OR weak_coverage
    flag_reasons: list


# ---------------------------------------------------------------------------
# One drafted impact-assessment narrative (produced by narrate.py from the tool call)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImpactNarrative:
    assessment: str
    relevant_controls_explained: list   # list of {"control_id": str, "explanation": str}
    gap_explanation: Optional[str]       # present when gap_flagged; None otherwise
    review_required_statement: str       # the model's own restatement that legal/compliance review is required
    citations: list                      # citation labels relied on; [] when ungrounded

    def to_dict(self) -> dict:
        return {
            "assessment": self.assessment,
            "relevant_controls_explained": list(self.relevant_controls_explained),
            "gap_explanation": self.gap_explanation,
            "review_required_statement": self.review_required_statement,
            "citations": list(self.citations),
        }


# ---------------------------------------------------------------------------
# The triage report submitted for approval
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TriageReport:
    requirement_text: str
    requirement_reference: Optional[str]
    source_system: str
    generated_at: str
    policy: dict
    control_count: int
    relevances: list                  # list[ControlRelevance], every control
    relevant_controls: list           # the surfaced subset
    coverage_verdict: str
    gap_flagged: bool
    flag_reasons: list
    narrative: Optional[ImpactNarrative]
    narrative_skipped_reason: Optional[str]
    model: Optional[str]
    narrative_prompt_hash: Optional[str]
    narrative_chunk_ids: list
    narrative_citations: list

    def summary(self) -> dict:
        return {
            "control_count": self.control_count,
            "relevant_control_count": len(self.relevant_controls),
            "coverage_verdict": self.coverage_verdict,
            "gap_flagged": self.gap_flagged,
            "flag_reasons": list(self.flag_reasons),
            "requirement_reference": self.requirement_reference,
            "relevant_controls": [
                {
                    "control_id": cr.control_id,
                    "category": cr.category,
                    "score": cr.score,
                    "matched_terms": list(cr.matched_terms),
                    "category_match": cr.category_match,
                }
                for cr in self.relevant_controls
            ],
        }

    def to_dict(self) -> dict:
        return {
            "requirement_text": self.requirement_text,
            "requirement_reference": self.requirement_reference,
            "source_system": self.source_system,
            "generated_at": self.generated_at,
            "policy": self.policy,
            "summary": self.summary(),
            "relevances": [cr.to_dict() for cr in self.relevances],
            "relevant_controls": [cr.to_dict() for cr in self.relevant_controls],
            "coverage_verdict": self.coverage_verdict,
            "gap_flagged": self.gap_flagged,
            "flag_reasons": list(self.flag_reasons),
            "narrative": self.narrative.to_dict() if self.narrative is not None else None,
            "narrative_skipped_reason": self.narrative_skipped_reason,
            "model": self.model,
            "narrative_prompt_hash": self.narrative_prompt_hash,
            "narrative_chunk_ids": list(self.narrative_chunk_ids),
            "narrative_citations": list(self.narrative_citations),
        }
