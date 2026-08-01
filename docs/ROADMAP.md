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
