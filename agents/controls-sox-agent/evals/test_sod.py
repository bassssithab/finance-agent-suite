from datetime import date
from decimal import Decimal

from connectors import JournalEntry

from controls_sox_agent import ControlPolicy, check_segregation_of_duties

POLICY = ControlPolicy(dual_approval_threshold=Decimal("50000"))


def je(
    entry_id="JE-1",
    amount="1000.00",
    preparer="alice",
    approver_1="bob",
    approver_2=None,
    account="6000",
):
    return JournalEntry(
        source_system="test_co",
        source_capability="journal_entries",
        entry_id=entry_id,
        date=date(2026, 7, 1),
        account=account,
        amount=Decimal(amount),
        currency="USD",
        preparer=preparer,
        approver_1=approver_1,
        approver_2=approver_2,
        raw={},
    )


def codes(result):
    return [v.code for v in result.violations]


def test_clean_single_approved_entry_below_threshold_passes():
    (result,) = check_segregation_of_duties([je()], POLICY)
    assert result.passed is True
    assert result.violations == []
    assert result.dual_approval_required is False


def test_clean_dual_approved_entry_above_threshold_passes():
    (result,) = check_segregation_of_duties(
        [je(amount="90000.00", approver_1="bob", approver_2="carol")], POLICY
    )
    assert result.passed is True
    assert result.dual_approval_required is True


def test_self_approval_is_flagged_at_any_amount():
    (result,) = check_segregation_of_duties([je(preparer="alice", approver_1="alice")], POLICY)
    assert codes(result) == ["preparer_is_approver"]
    assert "alice" in result.violations[0].detail


def test_preparer_as_second_approver_is_flagged():
    (result,) = check_segregation_of_duties(
        [je(amount="90000.00", preparer="alice", approver_1="bob", approver_2="alice")], POLICY
    )
    assert codes(result) == ["preparer_is_approver"]
    assert "approver_2" in result.violations[0].detail


def test_duplicate_approvers_flagged_below_threshold():
    (result,) = check_segregation_of_duties(
        [je(amount="1000.00", approver_1="bob", approver_2="bob")], POLICY
    )
    assert codes(result) == ["duplicate_approvers"]


def test_missing_second_approver_flagged_at_or_above_threshold():
    (below,) = check_segregation_of_duties([je(amount="49999.99", approver_2=None)], POLICY)
    assert below.passed is True

    (at,) = check_segregation_of_duties([je(amount="50000.00", approver_2=None)], POLICY)
    assert codes(at) == ["missing_second_approver"]

    (above,) = check_segregation_of_duties([je(amount="120000.00", approver_2=None)], POLICY)
    assert codes(above) == ["missing_second_approver"]


def test_no_approver_at_all_is_flagged():
    (result,) = check_segregation_of_duties([je(approver_1=None, approver_2=None)], POLICY)
    assert codes(result) == ["no_approver"]


def test_no_approver_takes_precedence_over_missing_second_approver():
    (result,) = check_segregation_of_duties(
        [je(amount="90000.00", approver_1=None, approver_2=None)], POLICY
    )
    assert codes(result) == ["no_approver"]


def test_name_comparison_ignores_case_and_surrounding_whitespace():
    (result,) = check_segregation_of_duties(
        [je(preparer="  Alice ", approver_1="alice")], POLICY
    )
    assert codes(result) == ["preparer_is_approver"]


def test_one_entry_can_carry_multiple_distinct_violations():
    # preparer == approver_1, approvers duplicated, and above threshold with
    # effectively one distinct approver.
    (result,) = check_segregation_of_duties(
        [je(amount="90000.00", preparer="alice", approver_1="alice", approver_2="alice")],
        POLICY,
    )
    assert set(codes(result)) == {"preparer_is_approver", "duplicate_approvers"}
    assert result.passed is False


def test_threshold_is_configurable():
    lenient = ControlPolicy(dual_approval_threshold=Decimal("200000"))
    (result,) = check_segregation_of_duties([je(amount="90000.00", approver_2=None)], lenient)
    assert result.passed is True
    assert result.dual_approval_required is False


def test_results_preserve_input_order():
    entries = [je(entry_id="JE-3"), je(entry_id="JE-1"), je(entry_id="JE-2")]
    results = check_segregation_of_duties(entries, POLICY)
    assert [r.entry_id for r in results] == ["JE-3", "JE-1", "JE-2"]
