# LedgerMind — AI Agents for Finance, Accounting & Compliance

> **One platform. Thirteen agents. Every finance workflow.**
> *(LedgerMind is a working name — see `docs/BRAND.md` for alternatives.)*

LedgerMind is a suite of domain-specific AI agents that automate the manual,
repetitive, evidence-heavy work inside corporate finance teams — while keeping
a human approver in the loop and an immutable audit trail behind every action.

## Why a suite, not twelve tools

Every agent in this repo runs on the same shared chassis (`/platform`):

| Shared layer | What it does |
|---|---|
| **Connectors** | ERP (NetSuite, SAP, QuickBooks, Xero, Dynamics 365), banks, document stores, email |
| **Knowledge** | RAG over accounting standards, company policies, prior workpapers, regulations |
| **Approvals** | Human-in-the-loop queues — no agent posts, pays, or files without sign-off |
| **Audit log** | Immutable, hash-chained record of every agent action, input, and approval |

Agents are thin domain layers on top. Build the chassis once → ship agents fast.

## The agent catalog

Legend: ✅ **Built & tested** — real code, passing evals, safe to demo.
🚧 **Planned** — scaffold only, not yet implemented.

### Transactional finance
| Agent | Status | Workflow | What it automates |
|---|---|---|---|
| [`ap-agent`](agents/ap-agent) | 🚧 | Procure-to-Pay | Invoice capture, 3-way match, GL coding, approval routing |
| [`ar-collections-agent`](agents/ar-collections-agent) | 🚧 | Order-to-Cash | Cash application, dunning, dispute triage, DSO analytics |
| [`expense-agent`](agents/expense-agent) | 🚧 | T&E | Receipt audit, policy checks, duplicate/fraud flags |

### Record-to-Report
| Agent | Status | Workflow | What it automates | What it demonstrates |
|---|---|---|---|---|
| [`reconciliation-agent`](agents/reconciliation-agent) | ✅ | R2R | Bank/balance-sheet/intercompany matching, exception handling | Deterministic matching plus a full approval/audit chain, zero LLM calls, zero setup — `python demo.py` |
| [`close-agent`](agents/close-agent) | 🚧 | Financial close | Close checklist, JE drafting, flux explanations | |
| [`fpa-agent`](agents/fpa-agent) | 🚧 | FP&A | Budget-vs-actual narratives, forecasting, board packs | |

### Assurance
| Agent | Status | Workflow | What it automates | What it demonstrates |
|---|---|---|---|---|
| [`audit-readiness-agent`](agents/audit-readiness-agent) | ✅ | External audit (auditee side) | PBC responses, evidence collection, tie-out indexing | **Flagship.** Chains `reconciliation-agent`'s audit log and `platform/knowledge` citations into a drafted PBC response — the cross-agent reuse story end to end |
| [`controls-sox-agent`](agents/controls-sox-agent) | 🚧 | SOX / internal controls | Control testing, walkthrough docs, deficiency tracking | |

### Tax & regulatory
| Agent | Status | Workflow | What it automates | What it demonstrates |
|---|---|---|---|---|
| [`tax-compliance-agent`](agents/tax-compliance-agent) | 🚧 | Indirect & direct tax | E-invoice/VAT validation, provision support, filing prep | |
| [`vat-treatment-agent`](agents/vat-treatment-agent) | ✅ | Indirect tax (VAT) | Classifies an invoice line's VAT treatment (standard/zero-rated/exempt/out-of-scope) | Cited VAT classification grounded strictly in `platform/knowledge`, drafted via Claude, gated on human approval |
| [`technical-accounting-agent`](agents/technical-accounting-agent) | ✅ | GAAP/IFRS research | Standard research, position memos (ASC 606/842/815) | Cited GAAP/IFRS Q&A grounded strictly in `platform/knowledge`, same knowledge-grounded/approval-gated pattern as `vat-treatment-agent` |
| [`regulatory-change-agent`](agents/regulatory-change-agent) | 🚧 | Reg change management | Monitors regulators, maps rules → obligations → controls | |
| [`financial-crime-agent`](agents/financial-crime-agent) | 🚧 | AML/KYC | KYC doc review, alert narratives, SAR drafting support | |

## Repo layout

```
finance-agent-suite/
├── platform/            # Shared chassis (connectors, RAG, approvals, audit log)
├── agents/              # One folder per agent (see agents/_template to add one)
├── docs/                # Architecture, brand, roadmap
└── README.md
```

## Demo

`demo.py` runs the reconciliation-agent end to end against sample data in
`sample_data/` and narrates each step to the terminal: transactions fetched,
matches found (exact vs. tolerance), exceptions flagged with reasons, the
report submitted for approval, a reviewer and approver signing off, and the
audit log's hash-chain verification. Stdlib only, no LLM calls, no setup:

```bash
python demo.py
```

The three knowledge-grounded, Claude-backed agents (`technical-accounting-agent`,
`audit-readiness-agent`, `vat-treatment-agent`) each ship a narrated
`manual_live_run.py` that makes one real Claude API call end to end — retrieval,
drafted answer with citations, approval submission, audit-log verification. They
need `ANTHROPIC_API_KEY` set and cost real tokens, so they're not run in CI; see
each agent's README for the exact command.

## Design principles

1. **Human-in-the-loop by default.** Agents draft; humans approve. Autonomy is earned per-task, per-customer.
2. **Every action is evidence.** If it can't be shown to an auditor, it didn't happen.
3. **Deterministic where it must be, generative where it helps.** Calculations run in code; language runs in the model.
4. **ERP-agnostic.** Connectors are pluggable; no agent hard-codes a system of record.

## Status

4 of 13 agents are built and tested end to end (see the ✅ rows in the catalog
above); the rest are scaffolds. See [`docs/ROADMAP.md`](docs/ROADMAP.md) for
build order and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the
technical design.
