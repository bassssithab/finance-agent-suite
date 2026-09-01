"""Deterministic first-pass relevance match between a regulatory requirement and
the company's internal-controls register.

This is a CRUDE keyword/category overlap, not legal reasoning (CLAUDE.md rule
#4): tokenising, stopword removal, a lightweight stemmer, the set intersection
that scores each control, the ranking and the coverage verdict all run in plain
code so the result is reproducible and testable. A keyword match does not mean a
control actually satisfies the requirement, and the absence of one does not
prove a real gap — `narrate.py`'s system prompt makes the model say exactly
that, and the whole output is a triage for a qualified legal/compliance
reviewer, never a compliance determination.

No ASC/IFRS or legal reference is encoded — this module does string matching, not
interpretation.
"""

import re

from connectors import InternalControl

from .models import ControlRelevance, TriagePolicy, TriageResult

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# True function words plus a few terms so generic they only add noise to a
# controls-vs-requirement overlap. Deliberately small; extend per run via
# TriagePolicy.extra_stopwords.
_STOPWORDS = frozenset({
    "the", "and", "for", "are", "was", "were", "with", "that", "this", "its",
    "from", "must", "shall", "will", "should", "would", "may", "can", "could",
    "any", "all", "each", "not", "nor", "but", "out",
    "within", "into", "onto", "upon", "over", "under", "between", "per", "via",
    "least", "more", "most", "than", "such", "then", "also", "only", "other",
    "when", "where", "which", "who", "whom", "what", "how",
    "have", "has", "had", "been", "being", "does", "did",
    "their", "them", "they", "there", "here", "these", "those",
    "company", "companies", "requirement", "requirements",
    "control", "controls", "ensure", "ensures", "must",
})


def _stem(token: str) -> str:
    """A lightweight plural/gerund normaliser — NOT a real stemmer. Just enough
    to unify 'systems'/'system', 'documented'/'document', 'schedules'/'schedule'.
    """
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    if len(token) > 5 and token.endswith("ing"):
        return token[:-3]
    if len(token) > 4 and token.endswith("ed"):
        return token[:-2]
    return token


def key_terms(text: str, extra_stopwords: tuple = ()) -> set:
    """Deterministic set of key terms from free text: lower-case, split on
    non-alphanumerics, drop tokens shorter than 3, drop stopwords, stem, drop
    anything that stems shorter than 3."""
    stops = _STOPWORDS | {s.lower() for s in extra_stopwords}
    terms = set()
    for raw in _TOKEN_RE.findall(text.lower()):
        if len(raw) < 3 or raw in stops:
            continue
        stemmed = _stem(raw)
        if len(stemmed) >= 3 and stemmed not in stops:
            terms.add(stemmed)
    return terms


def assess_impact(
    requirement_text: str,
    controls: list[InternalControl],
    *,
    policy: TriagePolicy,
) -> TriageResult:
    req_terms = key_terms(requirement_text, policy.extra_stopwords)

    relevances: list[ControlRelevance] = []
    for control in controls:
        desc_terms = key_terms(control.description, policy.extra_stopwords)
        cat_terms = key_terms(control.category, policy.extra_stopwords)
        control_terms = desc_terms | cat_terms

        matched = req_terms & control_terms
        category_match = bool(req_terms & cat_terms)
        score = len(matched)

        relevances.append(ControlRelevance(
            control_id=control.control_id,
            description=control.description,
            category=control.category,
            score=score,
            matched_terms=sorted(matched),
            category_match=category_match,
            relevant=score >= policy.min_keyword_overlap,
        ))

    relevances.sort(key=lambda cr: (-cr.score, cr.control_id))
    relevant = [cr for cr in relevances if cr.relevant]
    surfaced = relevant[: policy.max_controls_surfaced]

    if not relevant:
        verdict = "likely_gap"
        gap_flagged = True
        flag_reasons = [
            f"no existing control shares at least {policy.min_keyword_overlap} key "
            "terms with the requirement — a likely gap for a reviewer to confirm"
        ]
    elif relevant[0].score >= policy.strong_overlap:
        verdict = "apparent_coverage"
        gap_flagged = False
        flag_reasons = []
    else:
        verdict = "weak_coverage"
        gap_flagged = True
        top = relevant[0]
        flag_reasons = [
            f"the strongest apparent match is {top.control_id} on only {top.score} "
            f"shared key term(s) ({', '.join(top.matched_terms)}), below the "
            f"{policy.strong_overlap}-term bar for a solid apparent match — coverage "
            "may be thin and needs a reviewer's judgement"
        ]

    return TriageResult(
        relevances=relevances,
        relevant_controls=surfaced,
        coverage_verdict=verdict,
        gap_flagged=gap_flagged,
        flag_reasons=flag_reasons,
    )
