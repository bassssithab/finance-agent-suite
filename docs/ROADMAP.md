# Roadmap

## Build order (recommended)

Do **not** build all twelve at once. Sequence by (a) chassis reuse,
(b) demo-ability, (c) willingness to pay.

### Phase 0 — Chassis (weeks 1–6)
- One ERP connector (QuickBooks or Xero — easiest APIs) + one document connector
- Minimal RAG pipeline with citations
- Approval queue UI
- Hash-chained audit log

### Phase 1 — First two agents (weeks 6–16)
1. **reconciliation-agent** — bounded problem, objective accuracy metrics,
   instantly demo-able, exercises ERP + bank connectors
2. **technical-accounting-agent** — pure document/RAG play, no write access
   needed, exercises the knowledge layer, low risk

### Phase 2 — The wedge product (months 4–8)
3. **audit-readiness-agent** — the highest-conviction commercial opportunity
   (see original research). Reuses recon outputs + knowledge layer + audit log.
   This becomes the flagship.

### Phase 3 — Expand across the close (months 8–14)
4. close-agent
5. ap-agent
6. controls-sox-agent

### Phase 4 — Regulatory & specialist (months 14–24)
7. tax-compliance-agent (time to e-invoicing mandate wave: FR/PL/UAE 2026–27)
8. regulatory-change-agent
9. fpa-agent, ar-collections-agent, expense-agent, financial-crime-agent

## Phase 5 — AI-Augmented Accounting Department (new direction)

Following a conversation with leadership, the project's direction has
expanded beyond a personal demo into a genuine internal tool concept. This
phase captures that vision. Everything in this phase follows the project's
existing discipline: deterministic core where possible, cited/guarded LLM
narrative where judgment is needed, human approval always the final gate,
prototype-and-fictional-data-first before anything touches real company
systems.

### The vision

An accounting function where any entry — however complex — can be understood
by someone with no accounting background just by reading it, and where a
familiar (accounting-literate) reader gets the same clarity with more depth.
Entries should be explainable against a configurable accounting framework
(GAAP, IFRS, UAE-specific rules, etc.) — the same corpus-selection mechanism
already used to switch between the fictional Larenthia VAT corpus and the
real UAE VAT corpus extends naturally to this.

### Chat-based capture (Telegram first, WhatsApp later)

A user texts or photographs an expense, purchase, or other transaction via
Telegram. An agent (reusing the existing vision-extraction pattern from
ap-agent / expense-agent) reads it, drafts the appropriate entry — invoice,
purchase bill, expense claim — and attaches the original photo/document as
evidence. The draft goes through the existing platform/approvals workflow
before anything is considered final.

**Security foundation (built):** platform/telegram-link associates a
Telegram chat_id with an existing platform/auth.User via a one-time,
single-use, expiring linking code — so every chat-originated entry is
attributed to a real, known user, and normal segregation-of-duties rules
(preparer ≠ approver) apply automatically via the existing approvals layer.

WhatsApp is a deliberate later step — the Business API requires Meta
verification and typically a paid intermediary provider, meaningfully more
setup than Telegram's free bot API. Prove the pattern on Telegram first.

### Complex entries via chat

The same chat interface should support describing (via text, optionally with
a supporting document/photo) more sophisticated transactions requiring real
accounting judgment: depreciation entries, payroll/compound entries, asset
disposals with accumulated depreciation, intercompany eliminations,
provisions for doubtful debts, asset impairment write-downs, fair value
adjustments. Each of these becomes its own agent (or extension of an
existing one), following the established pattern: deterministic calculation
where the numbers are mechanical, cited narrative explanation grounded in
the selected accounting framework, and mandatory human review before
anything is final — no different in spirit from close-agent or
controls-sox-agent, just applied to harder entries.

### The explicit boundary — no real ERP writes yet

Every piece of this phase stops at drafting an entry for human approval.
Actually posting an approved entry into a real ERP (e.g., Zoho) is a
deliberately separate, later, and more carefully-governed step: it requires
a real Zoho connector (ideally built and tested against a Zoho
sandbox/developer account, not production data), and explicit sign-off —
likely involving IT — before it ever touches a real company's live
accounting system. This boundary is not a technical limitation; it is a
deliberate governance decision consistent with the project's entire
philosophy: agents draft, humans approve, and the step from "draft" to
"real system of record" gets the most scrutiny of all.

### Status

- [x] `platform/telegram-link` — chat_id ↔ user linking, one-time codes, audit-logged
- [ ] A real (test) Telegram bot wired to `platform/telegram-link`
- [ ] Bot photo/text input → existing expense-agent / ap-agent extraction → draft (still no ERP write)
- [ ] Accounting-framework selection formalized as a user-facing setting across agents
- [ ] Complex-entry agents (depreciation, payroll, intercompany, provisions, impairment, fair value) — each scoped and built individually, with the same care as vat-treatment-agent's original scoping
- [ ] A real Zoho connector — explicitly gated on sandbox testing and IT/security sign-off; not started

## Milestones
- [ ] Chassis MVP running end-to-end on sample data
- [ ] Recon agent passes eval suite (>95% match precision on golden set)
- [ ] First design partner using recon agent on live data
- [ ] Audit-readiness agent responds to a real PBC list
- [ ] SOC 2 Type I report
- [ ] 3 paying customers on flagship agent

## Later / research
- Cross-agent workflows (close-agent hands exceptions to recon-agent)
- Auditor-side portal (two-sided network for audit-readiness)
- Fine-tuned extraction models where volume justifies it
