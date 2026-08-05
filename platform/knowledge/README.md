# platform/knowledge

Shared chassis component. See docs/ARCHITECTURE.md.

Retrieval over accounting standards, company policies, prior-period
workpapers, regulations, and contracts. Agents retrieve through
`KnowledgeBase` rather than querying a store directly (CLAUDE.md golden
rule #1), and every `SearchResult` carries a `Chunk.citation` back to its
source document — citations are mandatory in generated output.

## What's implemented

- **Chunking** (`chunking.chunk_document`): paragraph-first splitting, packs
  whole paragraphs up to `max_chars` and only splits mid-paragraph when a
  single paragraph exceeds the limit on its own.
- **Retrieval** (`bm25.BM25Index`): Okapi BM25, stdlib only (`math`, `re`,
  `collections`) — no embedding model or vector store dependency.
- **`KnowledgeBase`**: ties ingestion (chunk) and retrieval (index) together.
  `ingest()` is additive and keyed by `doc_id`, so re-ingesting a `doc_id`
  replaces it rather than duplicating chunks.

## Deferred (tracked gaps, not yet built)

- **Vector half of hybrid retrieval.** docs/ARCHITECTURE.md specifies hybrid
  BM25 + vector search. Only the BM25 half exists; there's no embedding
  step or vector index yet.
- **Metadata filters.** `Document.metadata` / `Chunk.metadata` carry fields
  like `period`, `entity`, `framework`, but `KnowledgeBase.search()` does
  not filter on them — every search runs over the full corpus. Add filtering
  when an agent has a real need for it (e.g. "only FY2026 policies").
- **Real corpus.** Test fixtures (`tests/fixtures.py`) are synthetic,
  clearly-labeled placeholder text, not real ASC/IFRS excerpts or actual
  company policies. Swap in real excerpts before any agent relies on this
  for an actual accounting determination.

## Usage

```python
from knowledge import Document, KnowledgeBase

kb = KnowledgeBase()
kb.ingest([
    Document(
        doc_id="policy-travel-2026",
        title="Employee Travel & Expense Policy",
        corpus="policy",
        text="...",
        metadata={"period": "FY2026"},
    ),
])

results = kb.search("mileage reimbursement rate", top_k=3)
for result in results:
    print(result.score, result.chunk.citation)
    print(result.chunk.text)
```

## Development

```bash
# from repo root, one-time setup
python3 -m venv .venv
.venv/bin/pip install pytest

# run tests
cd platform/knowledge && ../../.venv/bin/pytest -v
```

No install step is needed — `conftest.py` puts `knowledge/` on `sys.path`
for the test run.
