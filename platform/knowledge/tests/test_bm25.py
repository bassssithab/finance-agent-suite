from knowledge import BM25Index, Chunk, tokenize


def make_chunk(chunk_id, text, doc_id="d1"):
    return Chunk(
        chunk_id=chunk_id, doc_id=doc_id, doc_title="Doc", corpus="policy",
        position=0, text=text,
    )


def test_tokenize_lowercases_and_strips_punctuation():
    assert tokenize("Lease Liability, and Right-of-Use!") == [
        "lease", "liability", "and", "right", "of", "use",
    ]


def test_search_ranks_more_relevant_chunk_first():
    index = BM25Index()
    lease_chunk = make_chunk("c1", "The lessee recognizes a lease liability at commencement.")
    travel_chunk = make_chunk("c2", "Employees submit a travel expense reimbursement request.")
    index.build([lease_chunk, travel_chunk])

    results = index.search("lease liability")

    assert results[0].chunk.chunk_id == "c1"
    assert results[0].score > 0
    assert len(results) == 1  # travel_chunk shares no query terms, scores 0, excluded


def test_search_respects_top_k():
    index = BM25Index()
    chunks = [make_chunk(f"c{i}", "lease liability lease liability") for i in range(10)]
    index.build(chunks)

    results = index.search("lease", top_k=3)

    assert len(results) == 3


def test_empty_query_returns_no_results():
    index = BM25Index()
    index.build([make_chunk("c1", "some text")])

    assert index.search("") == []
    assert index.search("   ") == []


def test_query_with_no_matching_terms_returns_no_results():
    index = BM25Index()
    index.build([make_chunk("c1", "lease liability")])

    assert index.search("zzz nonexistent") == []


def test_empty_index_returns_no_results():
    index = BM25Index()
    index.build([])

    assert index.search("anything") == []


def test_higher_term_frequency_scores_higher():
    index = BM25Index()
    frequent = make_chunk("c1", "lease lease lease liability payments obligations schedules terms")
    sparse = make_chunk("c2", "lease liability payments obligations schedules terms extra words here")
    index.build([frequent, sparse])

    results = index.search("lease")

    assert results[0].chunk.chunk_id == "c1"
