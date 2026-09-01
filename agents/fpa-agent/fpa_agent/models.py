"""Data model for the fpa-agent driver-based forecast workflow: the forward
assumptions, one projected line item, the drafted narrative, and the forecast
report that goes to approvals.

A `ForecastReport` is never a committed plan by itself — CLAUDE.md rule #2
requires it to go through `platform/approvals` before anything is acted on. This
module only shapes the data and its audit/approval payloads; the deterministic
projection lives in `projection.py` (plain code, rule #4), the narrative
drafting in `narrate.py` (the only LLM call), orchestration in `runner.py`.

A forecast is a forward-looking projection built on assumptions, not a
prediction. Every `growth_rate` on a `ProjectedLine` is an assumption the
planner supplied, not a measured trend — `narrate.py`'s system prompt makes the
model say so.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

# ---------------------------------------------------------------------------
# Forward assumptions (consumed by projection.py) — all configurable per run
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ForecastAssumptions:
    """The forward-looking inputs to the projection.

    `default_growth` is the per-period growth rate applied to any line whose
    category is not in `category_growth`. Rates are decimals: `Decimal("0.03")`
    is 3% per period, compounded. `max_pop_change_pct` is compared against the
    absolute assumed rate — a line whose rate is at or above it is flagged
    `high_sensitivity` for extra scrutiny. `flag_negative` flags any line that
    a growth rate drives below zero.

    `horizon` (how many periods to project) is a `run_driver_based_forecast`
    argument, not an assumption — it does not change the shape of any line, only
    how far each is carried.
    """

    default_growth: Decimal = Decimal("0.02")
    category_growth: dict = field(default_factory=dict)   # {category: Decimal}
    max_pop_change_pct: Decimal = Decimal("0.25")
    flag_negative: bool = True

    def to_dict(self) -> dict:
        return {
            "default_growth": str(self.default_growth),
            "category_growth": {k: str(v) for k, v in self.category_growth.items()},
            "max_pop_change_pct": str(self.max_pop_change_pct),
            "flag_negative": self.flag_negative,
        }


# ---------------------------------------------------------------------------
# One projected line (produced by projection.py) — pure arithmetic, no LLM
# ---------------------------------------------------------------------------

# Flag codes, each at most once per projected line:
#   "high_sensitivity"          — |assumed growth rate| >= max_pop_change_pct
#   "projection_turns_negative" — the assumed rate drives this period below zero
#   "stale_base"                — the base value was carried forward from a
#                                 period earlier than the overall base period
FLAG_CODES = ("high_sensitivity", "projection_turns_negative", "stale_base")


@dataclass(frozen=True)
class ProjectedLine:
    account: str
    line_item: str
    category: str
    currency: str
    base_period: str                 # the period the base_amount was taken from
    base_amount: Decimal
    period: str                      # the projected period, e.g. "2026-07"
    period_index: int                # 1..horizon
    growth_rate: Decimal             # the assumed per-period rate applied
    growth_source: str               # "category" | "default"
    projected_amount: Decimal
    change_vs_base: Decimal          # projected_amount - base_amount
    pct_change_vs_base: Optional[Decimal]  # None when base_amount is 0
    flagged: bool
    flag_reasons: list

    def to_dict(self) -> dict:
        return {
            "account": self.account,
            "line_item": self.line_item,
            "category": self.category,
            "currency": self.currency,
            "base_period": self.base_period,
            "base_amount": str(self.base_amount),
            "period": self.period,
            "period_index": self.period_index,
            "growth_rate": str(self.growth_rate),
            "growth_source": self.growth_source,
            "projected_amount": str(self.projected_amount),
            "change_vs_base": str(self.change_vs_base),
            "pct_change_vs_base": (
                str(self.pct_change_vs_base) if self.pct_change_vs_base is not None else None
            ),
            "flagged": self.flagged,
            "flag_reasons": list(self.flag_reasons),
        }


# ---------------------------------------------------------------------------
# One drafted narrative (produced by narrate.py from the model's tool call)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ForecastNarrative:
    summary: str                       # the overall trajectory, in plain words
    assumptions_described: list        # each key assumption, restated AS an assumption
    flagged_items_called_out: list     # one line per flagged high-sensitivity item
    citations: list                    # citation labels relied on; [] when ungrounded

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "assumptions_described": list(self.assumptions_described),
            "flagged_items_called_out": list(self.flagged_items_called_out),
            "citations": list(self.citations),
        }


# ---------------------------------------------------------------------------
# The forecast report submitted for approval
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ForecastReport:
    source_system: str
    generated_at: str
    currency: str
    base_period: str
    horizon: int
    assumptions: dict
    projected_periods: list             # the horizon future period labels
    projected_lines: list               # every (account, line_item) x every future period
    narrative: Optional[ForecastNarrative]
    narrative_skipped_reason: Optional[str]   # None if drafting ran and parsed
    model: Optional[str]
    narrative_prompt_hash: Optional[str]
    narrative_chunk_ids: list
    narrative_citations: list

    @property
    def flagged(self) -> list:
        return [pl for pl in self.projected_lines if pl.flagged]

    def summary(self) -> dict:
        final_period = self.projected_periods[-1] if self.projected_periods else None

        base_by_line: dict = {}
        final_by_line: dict = {}
        for pl in self.projected_lines:
            key = (pl.account, pl.line_item)
            base_by_line[key] = pl.base_amount
            if pl.period == final_period:
                final_by_line[key] = pl.projected_amount

        total_base = sum(base_by_line.values(), Decimal("0"))
        total_final = sum(final_by_line.values(), Decimal("0"))

        # base once per line, final once per line (at the final projected period)
        by_category: dict = {}
        seen_base: set = set()
        for pl in self.projected_lines:
            cat = pl.category or "(uncategorised)"
            entry = by_category.setdefault(cat, {"base": Decimal("0"), "final": Decimal("0")})
            key = (pl.account, pl.line_item)
            if key not in seen_base:
                entry["base"] += pl.base_amount
                seen_base.add(key)
            if pl.period == final_period:
                entry["final"] += pl.projected_amount

        def _pct(base: Decimal, final: Decimal) -> Optional[str]:
            if base == 0:
                return None
            return str(((final - base) / base).quantize(Decimal("0.0001")))

        return {
            "line_count": len({(pl.account, pl.line_item) for pl in self.projected_lines}),
            "row_count": len(self.projected_lines),
            "flagged_count": len(self.flagged),
            "base_period": self.base_period,
            "projected_periods": list(self.projected_periods),
            "total_base": str(total_base),
            "total_projected_final": str(total_final),
            "total_growth_pct_over_horizon": _pct(total_base, total_final),
            "by_category": {
                cat: {
                    "base": str(v["base"]),
                    "final": str(v["final"]),
                    "growth_pct_over_horizon": _pct(v["base"], v["final"]),
                }
                for cat, v in sorted(by_category.items())
            },
            "flagged_lines": [
                {
                    "account": pl.account,
                    "line_item": pl.line_item,
                    "category": pl.category,
                    "period": pl.period,
                    "growth_rate": str(pl.growth_rate),
                    "growth_source": pl.growth_source,
                    "projected_amount": str(pl.projected_amount),
                    "flag_reasons": list(pl.flag_reasons),
                }
                for pl in self.flagged
            ],
        }

    def to_dict(self) -> dict:
        return {
            "source_system": self.source_system,
            "generated_at": self.generated_at,
            "currency": self.currency,
            "base_period": self.base_period,
            "horizon": self.horizon,
            "assumptions": self.assumptions,
            "projected_periods": list(self.projected_periods),
            "summary": self.summary(),
            "projected_lines": [pl.to_dict() for pl in self.projected_lines],
            "narrative": self.narrative.to_dict() if self.narrative is not None else None,
            "narrative_skipped_reason": self.narrative_skipped_reason,
            "model": self.model,
            "narrative_prompt_hash": self.narrative_prompt_hash,
            "narrative_chunk_ids": list(self.narrative_chunk_ids),
            "narrative_citations": list(self.narrative_citations),
        }
