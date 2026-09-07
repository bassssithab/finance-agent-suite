"""A simulated week of usage for one fictional tenant.

Nothing here is real. WEEK_START is a Monday; the week is 5 working days
(offsets 0-4). SIMULATED_WEEK is the single source of truth — the tests
recompute every expected number from it with collections.Counter, and also
assert a few hand-verified anchors.

Documented totals for acme-books:
    login          : 15   (3 users x 5 mornings)
    agent_run      : 23   (dana 12 + dana's Wed burst 6 + evan 5)
    file_uploaded  : 2    (farah, Tue + Thu)
    -------------------
    per user       : dana.acme 23 | evan.acme 10 | farah.acme 7   (= 40)
    agent_run only : dana.acme 18 | evan.acme 5
"""

from datetime import datetime, timedelta, timezone

WEEK_START = datetime(2026, 9, 7, 0, 0, 0, tzinfo=timezone.utc)  # a Monday

FICTIONAL_TENANTS = [
    ("acme-books", "Acme Bookkeeping LLC"),
    ("globex-finance", "Globex Finance Co"),
]

ACME_USERS = ("dana.acme", "evan.acme", "farah.acme")

# (day_offset, hour, minute, username, event_type, detail)
SIMULATED_WEEK = [
    # each user logs in once every working morning
    *[(d, 9, i * 5, user, "login", "web")
      for d in range(5) for i, user in enumerate(ACME_USERS)],

    # dana: 3 agent runs on Mon/Tue/Thu/Fri
    *[(d, hour, 0, "dana.acme", "agent_run", agent)
      for d in (0, 1, 3, 4)
      for hour, agent in ((9, "reconciliation-agent"),
                          (11, "reconciliation-agent"),
                          (15, "ap-agent"))],
    # dana: a 6-run reconciliation burst on Wednesday afternoon (14:00-14:50)
    *[(2, 14, m, "dana.acme", "agent_run", "reconciliation-agent")
      for m in (0, 10, 20, 30, 40, 50)],

    # evan: one agent run a day
    *[(d, 10, 30, "evan.acme", "agent_run", "close-agent") for d in range(5)],

    # farah: two file uploads
    (1, 13, 0, "farah.acme", "file_uploaded", "receipt-jan.pdf"),
    (3, 13, 0, "farah.acme", "file_uploaded", "receipt-feb.pdf"),
]

# A little activity for the other tenant, for the isolation test.
SIMULATED_WEEK_GLOBEX = [
    (0, 9, 0, "greta.globex", "login", "web"),
    (0, 10, 0, "greta.globex", "agent_run", "vat-treatment-agent"),
    (2, 14, 15, "greta.globex", "agent_run", "vat-treatment-agent"),
]


def at(day: int, hour: int, minute: int) -> datetime:
    return WEEK_START + timedelta(days=day, hours=hour, minutes=minute)


def load(store, scope, rows=SIMULATED_WEEK) -> None:
    for day, hour, minute, username, event_type, detail in rows:
        store.record_event(
            scope, username=username, event_type=event_type, detail=detail,
            now=at(day, hour, minute),
        )
