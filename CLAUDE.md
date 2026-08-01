# CLAUDE.md — Finance Agent Suite

## What this project is
A monorepo of AI agents for finance, accounting and compliance teams, built on
one shared platform chassis. Read `README.md` for the product vision,
`docs/ARCHITECTURE.md` for the technical design, and `docs/ROADMAP.md` for
build order before starting any task.

## Golden rules (never violate)
1. **Agents never bypass the chassis.** All ERP/document/bank access goes
   through `platform/connectors`. All retrieval goes through
   `platform/knowledge`. No agent talks to external systems directly.
2. **Human-in-the-loop by default.** Any action with an external effect
   (posting a journal entry, sending an email, filing anything) must go
   through `platform/approvals`. Agents draft; humans approve.
3. **Everything is logged.** Every agent action writes to
   `platform/audit-log` (append-only). If a feature can't produce an audit
   trail, redesign it.
4. **Deterministic math, generative language.** Calculations, matching, and
   totals run in plain code with tests. The LLM writes explanations,
   narratives, and classifications — never arithmetic.
5. **One agent per folder.** Work on `agents/<name>/` only for the agent
   requested. Never modify another agent's folder in the same session.
   Shared logic goes in `platform/`, nowhere else.

## Conventions
- Language: Python 3.11+, FastAPI for services, type hints everywhere
- Tests: pytest; every agent task needs a test in `agents/<name>/evals/`
  before it is considered done
- Config: environment variables via `.env` (never commit secrets;
  `.gitignore` already excludes it)
- New agents must copy the structure in `agents/_template/`
  (README.md, agent.yaml, tasks/, evals/)

## Workflow for every session
1. State which agent or platform module you are working on
2. Propose a plan before writing code; wait for approval on anything large
3. Write code + tests together
4. Run the tests and show results
5. Suggest a git commit message when the task is complete

## Build order (do not skip ahead)
Chassis first → reconciliation-agent → technical-accounting-agent →
audit-readiness-agent (flagship) → everything else per docs/ROADMAP.md.

## Don'ts
- Don't build multiple agents in one session
- Don't add new dependencies without stating why
- Don't mock the approvals or audit-log layers to "save time"
- Don't invent accounting rules — cite the standard (ASC/IFRS reference) in
  comments when logic encodes an accounting treatment
