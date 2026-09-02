# Architecture

## High-level design

```
┌────────────────────────────────────────────────────────────┐
│                      Agent Layer                           │
│  ap · ar · expense · recon · close · fpa · audit · sox ·   │
│  tax · technical-accounting · reg-change · fin-crime       │
├────────────────────────────────────────────────────────────┤
│                    Shared Platform                         │
│  ┌─────────────┐ ┌───────────┐ ┌───────────┐ ┌──────────┐  │
│  │ Connectors  │ │ Knowledge │ │ Approvals │ │ Audit    │  │
│  │ ERP/bank/   │ │ RAG +     │ │ HITL      │ │ Log      │  │
│  │ docs/email  │ │ policies  │ │ queues    │ │ (immut.) │  │
│  └─────────────┘ └───────────┘ └───────────┘ └──────────┘  │
├────────────────────────────────────────────────────────────┤
│         Orchestration (agent runtime, tool calling)        │
├────────────────────────────────────────────────────────────┤
│   LLM providers · Vector DB · Postgres · Object storage    │
└────────────────────────────────────────────────────────────┘
```

## Components

### 1. Connectors (`platform/connectors`)
Pluggable adapters, one interface per capability:
- **ERP**: NetSuite, SAP, QuickBooks, Xero, Dynamics 365, Workday
- **Documents**: SharePoint, Google Drive, S3, email attachments
- **Banking**: bank statement feeds (BAI2/MT940/CAMT), open-banking APIs
- Read-first: agents get read access on day one; write access (posting JEs,
  sending emails) is gated behind the approvals layer.

### 2. Knowledge (`platform/knowledge`)
- Ingestion pipeline: parse → chunk → embed → index
- Corpora: accounting standards (GAAP/IFRS), company policies, prior-period
  workpapers, regulations, contracts
- Retrieval: hybrid (BM25 + vector) with metadata filters (period, entity,
  framework), citations mandatory in every generated output

### 3. Approvals (`platform/approvals`)
- Every agent output that has an external effect enters a review queue
- Reviewer actions (approve / edit / reject) feed back as training signal
- Role-based routing (preparer → reviewer → approver) mirrors finance
  segregation-of-duties

### 4. Audit log (`platform/audit-log`)
- Append-only, hash-chained event store
- Records: inputs retrieved, model/version used, prompt hash, output,
  human approver, timestamp
- Exportable as auditor-readable evidence packs

## Identity & Access (prototype)

A layer distinct from the core chassis above. The four chassis components —
connectors, knowledge, approvals, audit-log — are what all twelve agents
run on. The three modules here are **not** part of that chassis: they are a
prototype identity/access layer, **not wired into `app.py` or any agent**,
built and tested entirely on fictional users and organizations, no real
company data. They work out how sign-in and per-organization data isolation
should fit the chassis before any of it becomes load-bearing.

### Composition

Three modules, composed in one direction:

```
platform/auth      identity      username + PBKDF2-hashed password (never
                                 plaintext) + random session tokens (only
                                 the token's hash is stored)
      │
platform/tenancy   organization  one Tenant per user; a TenantScope
                                 capability object + ScopedTable make the
                                 `WHERE tenant_id = ?` filter structurally
                                 unforgettable — the hand-rolled stand-in
                                 for Postgres row-level security
      │
platform/session   usable flow   SessionService.authenticate() runs the
                                 auth login, then resolves the user's
                                 tenant, and returns one bundle: session
                                 token + User + ready-to-use TenantScope.
                                 validate() re-derives that bundle from a
                                 token alone; logout() ends it.
```

`auth.Role` is reused from `platform/approvals`, so the chassis keeps one
role vocabulary (`preparer` / `reviewer` / `approver`).

### Audit trail

All three write to the same append-only, hash-chained `AuditLogStore` the
agents use (the audit-log component above), injected as a constructor
parameter — never constructed internally:

- `session.authenticate` → `session.login.succeeded` /
  `session.login.failed.bad_credentials` / `session.login.failed.no_tenant`
- `session.validate` → `session.validate.succeeded` /
  `session.validate.failed.invalid_token` /
  `session.validate.failed.no_tenant`
- `session.logout` → `session.logout`
- `tenancy.ScopedTable.insert` → `tenancy.scoped_insert` (scoped **writes**
  only; reads are deliberately not logged, to keep the chain's volume sane)

Passwords are never logged. Raw session tokens are never logged — events
carry a `sha256(token)[:12]` fingerprint, enough to correlate one session's
events but not to replay it from an exported evidence pack.

### Demonstration

`infra_login_demo.py` (repo root) is a standalone Streamlit app for this
layer, separate from `app.py`. It seeds the fictional users and tenants in
throwaway SQLite, runs the real `SessionService`, shows all three
`authenticate` outcomes (success, bad credentials, valid-but-unassigned),
persists login across reruns via a token in `st.session_state`, and
includes a tenant-scoped notes demo and an "Activity log" panel with a live
`verify_chain()` check. Run: `streamlit run infra_login_demo.py`.

## Agent contract

Each agent folder must contain:
- `agent.yaml` — declared tools, connectors, autonomy level, approval rules
- `README.md` — scope, workflows covered, out-of-scope list
- `tasks/` — task definitions (the atomic units of work)
- `evals/` — golden test cases; an agent ships only when evals pass

## Tech stack (proposed)

| Layer | Choice | Notes |
|---|---|---|
| Agent runtime | Anthropic API (tool use) / LangGraph-style orchestration | Deterministic control flow, model does the language |
| Vector DB | pgvector → dedicated store at scale | Start simple |
| App DB | Postgres | Multi-tenant, row-level security |
| Backend | Python (FastAPI) | Finance/ML library ecosystem |
| Frontend | React + TypeScript | Review queues, dashboards |
| Cloud | AWS or Azure | Azure eases enterprise/SOC2 sales |
| Auth | SSO (SAML/OIDC), SCIM | Enterprise requirement from day one |

## Security & compliance posture
- SOC 2 Type II track from the start (logging, access reviews, vendor mgmt)
- Customer data isolation per tenant; no cross-tenant training
- PII/financial data redaction options before model calls
- Data residency options (EU/UAE) for regulated customers
