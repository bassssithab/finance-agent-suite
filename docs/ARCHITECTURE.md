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
