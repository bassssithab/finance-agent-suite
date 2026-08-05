from knowledge import Document, KnowledgeBase

from fixtures import ALL_DOCUMENTS, LEASE_STANDARD, REVENUE_POLICY, TRAVEL_POLICY


def test_search_returns_chunks_from_the_most_relevant_document():
    kb = KnowledgeBase()
    kb.ingest(ALL_DOCUMENTS)

    results = kb.search("lease liability right-of-use asset")

    assert results
    assert results[0].chunk.doc_id == LEASE_STANDARD.doc_id


def test_search_result_carries_a_citation_back_to_its_source():
    kb = KnowledgeBase()
    kb.ingest(ALL_DOCUMENTS)

    results = kb.search("travel expense reimbursement receipt")

    assert results[0].chunk.doc_id == TRAVEL_POLICY.doc_id
    assert results[0].chunk.citation.startswith(TRAVEL_POLICY.title)


def test_top_k_limits_number_of_results():
    kb = KnowledgeBase()
    kb.ingest(ALL_DOCUMENTS)

    results = kb.search("policy customer performance obligation", top_k=1)

    assert len(results) == 1


def test_reingesting_same_doc_id_replaces_rather_than_duplicates():
    kb = KnowledgeBase()
    kb.ingest([REVENUE_POLICY])
    first_pass = kb.search("performance obligation")

    kb.ingest([REVENUE_POLICY])
    second_pass = kb.search("performance obligation")

    assert len(first_pass) == len(second_pass)


def test_ingest_is_additive_across_calls():
    kb = KnowledgeBase()
    kb.ingest([REVENUE_POLICY])
    kb.ingest([TRAVEL_POLICY])

    results = kb.search("travel expense mileage reimbursement")

    assert results[0].chunk.doc_id == TRAVEL_POLICY.doc_id


def test_no_results_for_a_completely_unrelated_query():
    kb = KnowledgeBase()
    kb.ingest([REVENUE_POLICY])

    assert kb.search("astronomy black holes supernova") == []


def test_documents_without_metadata_still_ingest_and_search():
    kb = KnowledgeBase()
    kb.ingest([Document(doc_id="d1", title="No Metadata Doc", corpus="policy", text="Widgets are great.")])

    results = kb.search("widgets")

    assert results[0].chunk.doc_id == "d1"
