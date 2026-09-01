import tempfile
from pathlib import Path

from connectors import FileInternalControlConnector, InternalControl

from regulatory_change_agent import TriagePolicy, assess_impact, key_terms
from fixtures import SCENARIOS

CONTROLS_DIR = Path(__file__).parent / "fixtures" / "controls"
DEFAULT_POLICY = TriagePolicy()


def ctl(control_id="CTL-1", description="Privileged access requires multi-factor authentication", category="Access Management"):
    return InternalControl(
        source_system="test_co",
        source_capability="internal_controls",
        control_id=control_id,
        description=description,
        category=category,
        raw={},
    )


def company_controls():
    return FileInternalControlConnector(
        source_system="larenthia", folder=CONTROLS_DIR
    ).fetch_controls()


def verdict(requirement, controls=None, policy=None):
    return assess_impact(
        requirement, controls or company_controls(), policy=policy or DEFAULT_POLICY
    )


# --- tokenisation -------------------------------------------------------


def test_key_terms_drops_stopwords_short_tokens_and_lowercases():
    terms = key_terms("The company MUST retain Records for 7 years")
    assert "the" not in terms and "must" not in terms and "company" not in terms
    assert "for" not in terms
    assert "record" in terms  # 'Records' -> lowercased, stemmed
    assert "retain" in terms
    assert "year" in terms


def test_light_stemming_unifies_plurals_and_tenses():
    assert key_terms("systems documented schedules") == key_terms("system document schedule")


# --- scoring ----------------------------------------------------------


def test_matched_terms_are_sorted_and_deterministic():
    result = verdict(
        "privileged access authentication",
        [ctl("CTL-1", "authentication for privileged access", "Access")],
    )
    cr = result.relevances[0]
    assert cr.matched_terms == sorted(cr.matched_terms)
    assert set(cr.matched_terms) == {"access", "authentication", "privileg"}
    assert cr.score == 3


def test_category_match_is_detected():
    result = verdict("access management policy update", [ctl("CTL-1", "user provisioning", "Access Management")])
    assert result.relevances[0].category_match is True
    # 'access' and 'management' overlap the category
    assert result.relevances[0].score >= 2


def test_relevance_uses_min_keyword_overlap_threshold():
    controls = [ctl("CTL-1", "authentication logging", "Security")]
    strict = verdict("authentication", controls, TriagePolicy(min_keyword_overlap=2))
    assert strict.relevances[0].relevant is False  # only 1 shared term
    lenient = verdict("authentication", controls, TriagePolicy(min_keyword_overlap=1))
    assert lenient.relevances[0].relevant is True


# --- verdicts -------------------------------------------------------


def test_likely_gap_when_nothing_reaches_the_threshold():
    result = verdict("wholly unrelated maritime salvage obligation", [ctl("CTL-1", "payroll runs monthly", "HR")])
    assert result.coverage_verdict == "likely_gap"
    assert result.gap_flagged is True
    assert result.relevant_controls == []
    assert any("no existing control" in r for r in result.flag_reasons)


def test_apparent_coverage_when_a_control_matches_strongly():
    result = verdict(
        "privileged system access must require multi-factor authentication recertified quarterly",
        [ctl("CTL-1", "privileged system access requires multi-factor authentication recertified quarterly", "Access Management")],
    )
    assert result.coverage_verdict == "apparent_coverage"
    assert result.gap_flagged is False
    assert result.flag_reasons == []


def test_weak_coverage_when_the_best_match_is_marginal():
    result = verdict(
        "documented data retention schedule with periodic disposal",
        [ctl("CTL-1", "data retention records are catalogued in a data inventory", "Data Protection")],
        TriagePolicy(min_keyword_overlap=2, strong_overlap=4),
    )
    assert result.coverage_verdict == "weak_coverage"
    assert result.gap_flagged is True
    assert any("strongest apparent match is CTL-1" in r for r in result.flag_reasons)


def test_surfaced_controls_are_capped_and_ranked_by_score_then_id():
    controls = [
        ctl("CTL-3", "access authentication logging review", "Access"),
        ctl("CTL-1", "access authentication logging", "Access"),
        ctl("CTL-2", "access authentication", "Access"),
    ]
    result = verdict("access authentication logging review", controls, TriagePolicy(max_controls_surfaced=2))
    assert [cr.control_id for cr in result.relevant_controls] == ["CTL-3", "CTL-1"]
    # CTL-3 scores 4, CTL-1 scores 3, CTL-2 scores 2; ranked by score then id, cap 2 drops CTL-2


# --- the three committed scenarios --------------------------------


def test_committed_scenarios_produce_the_expected_verdicts():
    for name, scenario in SCENARIOS.items():
        result = verdict(scenario["requirement_text"])
        assert result.coverage_verdict == scenario["expected_verdict"], name
        assert [cr.control_id for cr in result.relevant_controls] == scenario["expected_relevant_ids"], name
