import hashlib

import pytest

from connectors import (
    FileDocumentConnector,
    SourceDocument,
    UnknownDocumentError,
    UnsupportedMediaTypeError,
)

# A few bytes with a PNG signature — the connector keys off the file extension,
# not the content, so this doesn't need to be a real image.
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-invoice-image-payload"


@pytest.fixture
def docs_folder(tmp_path):
    folder = tmp_path / "invoices"
    folder.mkdir()
    (folder / "invoice_a.png").write_bytes(PNG_BYTES)
    (folder / "invoice_b.jpg").write_bytes(b"\xff\xd8\xff-fake-jpeg")
    (folder / "notes.txt").write_text("not a document type we support")
    return folder


def test_list_documents_returns_supported_files_sorted(docs_folder):
    connector = FileDocumentConnector(source_system="sample_co", folder=docs_folder)

    assert connector.list_documents() == ["invoice_a.png", "invoice_b.jpg"]


def test_list_documents_empty_when_folder_missing(tmp_path):
    connector = FileDocumentConnector(
        source_system="sample_co", folder=tmp_path / "no_such_folder"
    )

    assert connector.list_documents() == []


def test_fetch_document_returns_bytes_and_identity(docs_folder):
    connector = FileDocumentConnector(source_system="sample_co", folder=docs_folder)

    doc = connector.fetch_document("invoice_a.png")

    assert isinstance(doc, SourceDocument)
    assert doc.source_system == "sample_co"
    assert doc.source_capability == "documents"
    assert doc.document_id == "invoice_a.png"
    assert doc.filename == "invoice_a.png"
    assert doc.media_type == "image/png"
    assert doc.content == PNG_BYTES
    assert doc.size_bytes == len(PNG_BYTES)
    assert doc.sha256 == hashlib.sha256(PNG_BYTES).hexdigest()


def test_fetch_document_maps_jpg_to_jpeg_media_type(docs_folder):
    connector = FileDocumentConnector(source_system="sample_co", folder=docs_folder)

    assert connector.fetch_document("invoice_b.jpg").media_type == "image/jpeg"


def test_fetch_unknown_document_raises(docs_folder):
    connector = FileDocumentConnector(source_system="sample_co", folder=docs_folder)

    with pytest.raises(UnknownDocumentError):
        connector.fetch_document("missing.png")


def test_fetch_unsupported_type_raises(docs_folder):
    connector = FileDocumentConnector(source_system="sample_co", folder=docs_folder)

    with pytest.raises(UnsupportedMediaTypeError):
        connector.fetch_document("notes.txt")


@pytest.mark.parametrize("bad_id", ["../secret.png", "sub/dir.png", "/etc/passwd"])
def test_fetch_document_rejects_path_traversal(docs_folder, bad_id):
    connector = FileDocumentConnector(source_system="sample_co", folder=docs_folder)

    with pytest.raises(UnknownDocumentError):
        connector.fetch_document(bad_id)
