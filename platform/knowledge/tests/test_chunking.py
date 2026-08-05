from knowledge import Document, chunk_document

from fixtures import LEASE_STANDARD


def test_short_document_becomes_a_single_chunk():
    doc = Document(doc_id="d1", title="Short Doc", corpus="policy", text="One short paragraph.")

    chunks = chunk_document(doc)

    assert len(chunks) == 1
    assert chunks[0].text == "One short paragraph."
    assert chunks[0].chunk_id == "d1:0"
    assert chunks[0].doc_id == "d1"
    assert chunks[0].doc_title == "Short Doc"
    assert chunks[0].corpus == "policy"
    assert chunks[0].position == 0


def test_paragraphs_pack_together_up_to_max_chars():
    doc = Document(
        doc_id="d1", title="Doc", corpus="policy",
        text="Paragraph one.\n\nParagraph two.\n\nParagraph three.",
    )

    chunks = chunk_document(doc, max_chars=1000)

    assert len(chunks) == 1
    assert "Paragraph one." in chunks[0].text
    assert "Paragraph three." in chunks[0].text


def test_splits_into_new_chunk_once_max_chars_exceeded():
    doc = Document(
        doc_id="d1", title="Doc", corpus="policy",
        text="Paragraph one is here.\n\nParagraph two is here.\n\nParagraph three is here.",
    )

    chunks = chunk_document(doc, max_chars=40)

    assert len(chunks) == 3
    assert [c.position for c in chunks] == [0, 1, 2]
    assert [c.chunk_id for c in chunks] == ["d1:0", "d1:1", "d1:2"]
    assert all(len(c.text) <= 40 for c in chunks)


def test_paragraph_longer_than_max_chars_is_split_within_itself():
    doc = Document(doc_id="d1", title="Doc", corpus="policy", text="x" * 100)

    chunks = chunk_document(doc, max_chars=40)

    assert len(chunks) == 3
    assert [len(c.text) for c in chunks] == [40, 40, 20]
    assert "".join(c.text for c in chunks) == "x" * 100


def test_metadata_carried_from_document_to_chunk():
    doc = Document(
        doc_id="d1", title="Doc", corpus="standard", text="Some text.",
        metadata={"framework": "synthetic"},
    )

    chunks = chunk_document(doc)

    assert chunks[0].metadata == {"framework": "synthetic"}


def test_citation_references_title_corpus_and_position():
    doc = Document(doc_id="d1", title="My Policy", corpus="policy", text="Text here.")

    chunk = chunk_document(doc)[0]

    assert chunk.citation == "My Policy (policy), chunk 0"


def test_long_fixture_document_splits_into_multiple_chunks():
    chunks = chunk_document(LEASE_STANDARD)

    assert len(chunks) > 1
    assert all(c.doc_id == LEASE_STANDARD.doc_id for c in chunks)
    assert [c.position for c in chunks] == list(range(len(chunks)))
