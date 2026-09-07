from collections import Counter
from datetime import timedelta

import pytest

from tenancy import MissingTenantScope, TenancyStore
from analytics import AnalyticsStore, RateLimitStatus, UsageEvent

from fixtures import (
    FICTIONAL_TENANTS,
    SIMULATED_WEEK,
    SIMULATED_WEEK_GLOBEX,
    WEEK_START,
    at,
    load,
)

WEEK_END = WEEK_START + timedelta(days=7)


@pytest.fixture
def tenancy_store(tmp_path):
    s = TenancyStore(tmp_path / "tenancy.db")
    for tenant_id, display_name in FICTIONAL_TENANTS:
        s.create_tenant(tenant_id, display_name)
    yield s
    s.close()


@pytest.fixture
def scope_a(tenancy_store):
    return tenancy_store.scope_for("acme-books")


@pytest.fixture
def scope_b(tenancy_store):
    return tenancy_store.scope_for("globex-finance")


@pytest.fixture
def store(tmp_path):
    s = AnalyticsStore(tmp_path / "analytics.db")
    yield s
    s.close()


@pytest.fixture
def seeded(store, scope_a):
    load(store, scope_a)
    return store


# ---------------------------------------------------------------------------
# tenant isolation (same pattern as tenancy / file-storage)
# ---------------------------------------------------------------------------

def test_events_are_tenant_isolated(store, scope_a, scope_b):
    load(store, scope_a)
    load(store, scope_b, rows=SIMULATED_WEEK_GLOBEX)

    # acme queries never surface globex data
    assert all(e.tenant_id == "acme-books" for e in store.all_events(scope_a))
    assert "greta.globex" not in store.activity_by_user(scope_a)
    assert store.count_by_type(scope_a, start=WEEK_START, end=WEEK_END)["agent_run"] == 23

    # globex queries never surface acme data
    assert all(e.tenant_id == "globex-finance" for e in store.all_events(scope_b))
    assert store.count_by_type(scope_b, start=WEEK_START, end=WEEK_END) == {
        "login": 1, "agent_run": 2,
    }
    dana_seen_from_globex = store.rate_limit_check(
        scope_b, username="dana.acme", event_type="agent_run",
        limit=5, window_seconds=10 ** 9, now=WEEK_END,
    )
    assert dana_seen_from_globex.count == 0


def test_record_event_stamps_tenant_from_scope(store, scope_a):
    with pytest.raises(ValueError):
        store.record_event(
            scope_a, username="dana.acme", event_type="login", tenant_id="globex-finance"
        )
    store.record_event(scope_a, username="dana.acme", event_type="login")
    (event,) = store.all_events(scope_a)
    assert event.tenant_id == "acme-books"


def test_no_scope_fails_loudly(store):
    with pytest.raises(MissingTenantScope):
        store.record_event(None, username="x", event_type="login")
    with pytest.raises(MissingTenantScope):
        store.count_by_type(None, start=WEEK_START, end=WEEK_END)
    with pytest.raises(MissingTenantScope):
        store.activity_by_user("acme-books")  # a bare string is not a scope
    with pytest.raises(MissingTenantScope):
        store.rate_limit_check(
            None, username="x", event_type="agent_run", limit=1, window_seconds=60
        )


# ---------------------------------------------------------------------------
# aggregation counts — exact against the known simulated week
# ---------------------------------------------------------------------------

def test_count_by_type_matches_the_simulated_week(seeded, scope_a):
    counts = seeded.count_by_type(scope_a, start=WEEK_START, end=WEEK_END)

    assert counts == dict(Counter(row[4] for row in SIMULATED_WEEK))
    # hand-verified anchors
    assert counts["login"] == 15          # 3 users x 5 mornings
    assert counts["agent_run"] == 23      # dana 12 + burst 6 + evan 5
    assert counts["file_uploaded"] == 2


def test_count_by_type_respects_the_window(seeded, scope_a):
    wednesday = seeded.count_by_type(scope_a, start=at(2, 0, 0), end=at(3, 0, 0))
    # day 2: 3 logins + dana's 6-run burst + evan's 1 daily run
    assert wednesday == dict(Counter(row[4] for row in SIMULATED_WEEK if row[0] == 2))
    assert wednesday == {"login": 3, "agent_run": 7}

    before = seeded.count_by_type(
        scope_a, start=WEEK_START - timedelta(days=7), end=WEEK_START
    )
    assert before == {}


def test_activity_by_user_matches_the_simulated_week(seeded, scope_a):
    activity = seeded.activity_by_user(scope_a)

    assert activity == dict(Counter(row[3] for row in SIMULATED_WEEK))
    assert activity == {"dana.acme": 23, "evan.acme": 10, "farah.acme": 7}


def test_activity_by_user_with_an_event_type_filter(seeded, scope_a):
    agent_runs = seeded.activity_by_user(scope_a, event_type="agent_run")
    assert agent_runs == {"dana.acme": 18, "evan.acme": 5}   # farah has none


# ---------------------------------------------------------------------------
# rate-limit check — boundary behaviour
# ---------------------------------------------------------------------------

def test_rate_limit_check_at_the_boundary(seeded, scope_a):
    # dana's Wednesday burst: 6 reconciliation runs, 14:00-14:50
    after = at(2, 15, 0)  # 1h window from here covers all 6

    def check(limit):
        return seeded.rate_limit_check(
            scope_a, username="dana.acme", event_type="agent_run",
            limit=limit, window_seconds=3600, now=after,
        )

    assert check(5).count == 6
    assert isinstance(check(5), RateLimitStatus)

    assert check(5).exceeded is True and check(5).remaining == 0     # 6 >= 5
    assert check(6).exceeded is True and check(6).remaining == 0     # 6 >= 6, the exact boundary
    assert check(7).exceeded is False and check(7).remaining == 1    # 6 < 7

    # before the burst: nothing in the window
    early = seeded.rate_limit_check(
        scope_a, username="dana.acme", event_type="agent_run",
        limit=5, window_seconds=3600, now=at(2, 13, 55),
    )
    assert early.count == 0 and early.exceeded is False

    # partway through: only the 14:00 run is inside [13:05, 14:05]
    partway = seeded.rate_limit_check(
        scope_a, username="dana.acme", event_type="agent_run",
        limit=5, window_seconds=3600, now=at(2, 14, 5),
    )
    assert partway.count == 1 and partway.exceeded is False


def test_rate_limit_window_edge_is_inclusive(store, scope_a):
    now = WEEK_START + timedelta(days=1, hours=12)
    store.record_event(scope_a, username="u", event_type="ping",
                       now=now - timedelta(seconds=3600))   # exactly at the edge
    store.record_event(scope_a, username="u", event_type="ping",
                       now=now - timedelta(seconds=3601))   # one second outside

    status = store.rate_limit_check(
        scope_a, username="u", event_type="ping",
        limit=10, window_seconds=3600, now=now,
    )
    assert status.count == 1   # only the edge event, not the one 3601s old


def test_rate_limit_check_records_nothing(seeded, scope_a):
    before = len(seeded.all_events(scope_a))
    first = seeded.rate_limit_check(
        scope_a, username="dana.acme", event_type="agent_run",
        limit=5, window_seconds=3600, now=at(2, 15, 0),
    )
    second = seeded.rate_limit_check(
        scope_a, username="dana.acme", event_type="agent_run",
        limit=5, window_seconds=3600, now=at(2, 15, 0),
    )
    assert len(seeded.all_events(scope_a)) == before
    assert first == second


def test_all_events_are_usage_events_in_time_order(seeded, scope_a):
    events = seeded.all_events(scope_a)
    assert len(events) == len(SIMULATED_WEEK)
    assert all(isinstance(e, UsageEvent) for e in events)
    assert [e.occurred_at for e in events] == sorted(e.occurred_at for e in events)
