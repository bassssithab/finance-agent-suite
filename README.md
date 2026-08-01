# LedgerMind — AI Agents for Finance, Accounting & Compliance

> **One platform. Twelve agents. Every finance workflow.**
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

### Transactional finance
| Agent | Workflow | What it automates |
|---|---|---|
| [`ap-agent`](agents/ap-agent) | Procure-to-Pay | Invoice capture, 3-way match, GL coding, approval routing |
| [`ar-collections-agent`](agents/ar-collections-agent) | Order-to-Cash | Cash application, dunning, dispute triage, DSO analytics |
| [`expense-agent`](agents/expense-agent) | T&E | Receipt audit, policy checks, duplicate/fraud flags |

### Record-to-Report
| Agent | Workflow | What it automates |
|---|---|---|
| [`reconciliation-agent`](agents/reconciliation-agent) | R2R | Bank/balance-sheet/intercompany matching, exception handling |
| [`close-agent`](agents/close-agent) | Financial close | Close checklist, JE drafting, flux explanations |
| [`fpa-agent`](agents/fpa-agent) | FP&A | Budget-vs-actual narratives, forecasting, board packs |

### Assurance
| Agent | Workflow | What it automates |
|---|---|---|
| [`audit-readiness-agent`](agents/audit-readiness-agent) | External audit (auditee side) | PBC responses, evidence collection, tie-out indexing |
| [`controls-sox-agent`](agents/controls-sox-agent) | SOX / internal controls | Control testing, walkthrough docs, deficiency tracking |

### Tax & regulatory
| Agent | Workflow | What it automates |
|---|---|---|
| [`tax-compliance-agent`](agents/tax-compliance-agent) | Indirect & direct tax | E-invoice/VAT validation, provision support, filing prep |
| [`technical-accounting-agent`](agents/technical-accounting-agent) | GAAP/IFRS research | Standard research, position memos (ASC 606/842/815) |
| [`regulatory-change-agent`](agents/regulatory-change-agent) | Reg change management | Monitors regulators, maps rules → obligations → controls |
| [`financial-crime-agent`](agents/financial-crime-agent) | AML/KYC | KYC doc review, alert narratives, SAR drafting support |

## Repo layout

```
finance-agent-suite/
├── platform/            # Shared chassis (connectors, RAG, approvals, audit log)
├── agents/              # One folder per agent (see agents/_template to add one)
├── docs/                # Architecture, brand, roadmap
└── README.md
```

## Design principles

1. **Human-in-the-loop by default.** Agents draft; humans approve. Autonomy is earned per-task, per-customer.
2. **Every action is evidence.** If it can't be shown to an auditor, it didn't happen.
3. **Deterministic where it must be, generative where it helps.** Calculations run in code; language runs in the model.
4. **ERP-agnostic.** Connectors are pluggable; no agent hard-codes a system of record.

## Status

🚧 Early scaffold. See [`docs/ROADMAP.md`](docs/ROADMAP.md) for build order and
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the technical design.
