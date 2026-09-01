"""Deterministic driver-based projection of historical actuals.

No LLM involvement (CLAUDE.md rule #4): finding the base period, advancing the
calendar, applying the assumed growth rate, compounding it over the horizon, and
every threshold comparison run in plain `Decimal`/int code so the projection is
reproducible and testable. The model's only job, later, is to write prose about
the numbers this module produces — and to say plainly that they rest on
assumptions.

Periods are monthly `YYYY-MM` strings only (this iteration); anything else
raises `ValueError`. The base period is the most recent period present across
all lines. A line with no row in the base period is carried forward from its
most recent earlier period and flagged `stale_base`, so a real line is never
silently dropped from the forecast.

This is arithmetic on a company's own actuals and its own forward assumptions,
not an accounting treatment, so no ASC/IFRS reference is encoded (same stance as
`reconciliation_agent.matching`, `ap_agent.sanity` and `close_agent.variance`).
"""

import re
from decimal import ROUND_HALF_UP, Decimal

from connectors import BudgetActualLine

from .models import ForecastAssumptions, ProjectedLine

_PERIOD_RE = re.compile(r"^(\d{4})-(\d{2})$")
_MONEY_QUANTUM = Decimal("0.01")
_PCT_QUANTUM = Decimal("0.0001")


def _parse_period(period: str) -> tuple[int, int]:
    m = _PERIOD_RE.match(period or "")
    if not m:
        raise ValueError(
            f"period {period!r} is not a monthly YYYY-MM string; "
            "only monthly periods are supported this iteration"
        )
    year, month = int(m.group(1)), int(m.group(2))
    if not 1 <= month <= 12:
        raise ValueError(f"period {period!r} has an out-of-range month")
    return year, month


def _advance(period: str, n: int) -> str:
    """`period` plus `n` months, as a YYYY-MM string."""
    year, month = _parse_period(period)
    total = year * 12 + (month - 1) + n
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def _fmt_pct(ratio: Decimal) -> str:
    return f"{ratio * 100:.1f}%"


def project_forecast(
    actuals: list[BudgetActualLine],
    *,
    assumptions: ForecastAssumptions,
    horizon: int,
    currency: str,
) -> tuple[list[ProjectedLine], str, list[str]]:
    """Return (projected_lines, base_period, projected_periods).

    `projected_lines` has one `ProjectedLine` per (account, line_item) per
    future period, sorted by (account, line_item, period_index).
    """
    if horizon < 1:
        raise ValueError(f"horizon must be >= 1, got {horizon}")
    if not actuals:
        raise ValueError("no historical actuals to project from")

    periods = sorted({line.period for line in actuals}, key=_parse_period)
    base_period = periods[-1]
    projected_periods = [_advance(base_period, k) for k in range(1, horizon + 1)]

    # group by (account, line_item) -> {period: line}
    by_key: dict[tuple[str, str], dict[str, BudgetActualLine]] = {}
    for line in actuals:
        by_key.setdefault((line.account, line.line_item), {})[line.period] = line

    results: list[ProjectedLine] = []
    for account, line_item in sorted(by_key):
        rows = by_key[(account, line_item)]

        if base_period in rows:
            base_src_period = base_period
        else:
            # most recent period this line does appear in
            base_src_period = sorted(rows, key=_parse_period)[-1]
        base_line = rows[base_src_period]
        base_amount = base_line.amount
        category = base_line.category or next(
            (r.category for r in rows.values() if r.category), ""
        )

        rate = assumptions.category_growth.get(category, assumptions.default_growth)
        growth_source = "category" if category in assumptions.category_growth else "default"

        stale = base_src_period != base_period
        rate_flagged = abs(rate) >= assumptions.max_pop_change_pct

        for period_index, period in enumerate(projected_periods, start=1):
            factor = (Decimal("1") + rate) ** period_index
            projected_amount = (base_amount * factor).quantize(
                _MONEY_QUANTUM, rounding=ROUND_HALF_UP
            )
            change_vs_base = projected_amount - base_amount
            if base_amount == 0:
                pct_change_vs_base = None
            else:
                pct_change_vs_base = (change_vs_base / base_amount).quantize(
                    _PCT_QUANTUM, rounding=ROUND_HALF_UP
                )

            reasons: list[str] = []
            if rate_flagged:
                reasons.append(
                    f"assumed growth rate {_fmt_pct(abs(rate))} per period "
                    f">= sensitivity threshold {_fmt_pct(assumptions.max_pop_change_pct)}"
                )
            if assumptions.flag_negative and projected_amount < 0:
                reasons.append(
                    f"assumed growth rate drives {period} projection to "
                    f"{projected_amount} (below zero)"
                )
            if stale:
                reasons.append(
                    f"no actual for base period {base_period}; base carried "
                    f"forward from {base_src_period}"
                )

            results.append(ProjectedLine(
                account=account,
                line_item=line_item,
                category=category,
                currency=currency,
                base_period=base_src_period,
                base_amount=base_amount,
                period=period,
                period_index=period_index,
                growth_rate=rate,
                growth_source=growth_source,
                projected_amount=projected_amount,
                change_vs_base=change_vs_base,
                pct_change_vs_base=pct_change_vs_base,
                flagged=bool(reasons),
                flag_reasons=reasons,
            ))

    results.sort(key=lambda pl: (pl.account, pl.line_item, pl.period_index))
    return results, base_period, projected_periods
