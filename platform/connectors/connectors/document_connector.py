"""Local-folder stand-in for a real document store / email-attachment connector.

Read-first, same as `FileConnector`: hands an agent the raw bytes of a source
document (a scanned invoice, a receipt, a contract PDF) plus enough identity to
trace and cite it — nothing else. There are no write methods; anything an agent
produces from a document goes through `platform/approvals` (CLAUDE.md golden
rules #1 and #2).

Until Phase 1 wires a real document store (SharePoint / Drive / S3 / email —
see docs/ARCHITECTURE.md), `FileDocumentConnector` reads files straight off a
local folder, the same way `FileConnector` stands in for a live bank/ERP feed.
"""

import hashlib
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Union

from .models import SourceDocument

# Only document types an agent can currently send to a model. Kept deliberately
# small — an unknown extension is rejected rather than guessed at.
_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
}

SOURCE_CAPABILITY = "documents"


class UnknownDocumentError(Exception):
    """Raised when a requested document_id isn't present in the connector."""


class UnsupportedMediaTypeError(Exception):
    """Raised when a file's extension isn't a supported document type."""


class DocumentConnector(ABC):
    """Read-only interface for fetching binary source documents.

    Separate from `Connector` (which returns `Transaction` records): a document
    connector deals in raw files, not normalized rows.
    """

    @abstractmethod
    def list_documents(self) -> list[str]:
        """Return the ids of every fetchable document, sorted."""
        raise NotImplementedError

    @abstractmethod
    def fetch_document(self, document_id: str) -> SourceDocument:
        """Return one document's bytes plus its identity, or raise
        UnknownDocumentError / UnsupportedMediaTypeError."""
        raise NotImplementedError


class FileDocumentConnector(DocumentConnector):
    def __init__(self, source_system: str, folder: Union[str, Path]):
        self.source_system = source_system
        self.folder = Path(folder)

    def list_documents(self) -> list[str]:
        if not self.folder.is_dir():
            return []
        return sorted(
            p.name
            for p in self.folder.iterdir()
            if p.is_file() and p.suffix.lower() in _MEDIA_TYPES
        )

    def fetch_document(self, document_id: str) -> SourceDocument:
        # document_id is a bare filename within `folder` — reject anything with
        # path separators or parent references so it can't escape the folder.
        if Path(document_id).name != document_id:
            raise UnknownDocumentError(f"invalid document id {document_id!r}")

        path = self.folder / document_id
        if not path.is_file():
            raise UnknownDocumentError(
                f"no document {document_id!r} in {self.folder}"
            )

        suffix = path.suffix.lower()
        if suffix not in _MEDIA_TYPES:
            raise UnsupportedMediaTypeError(
                f"{document_id!r}: unsupported document type {suffix!r}"
            )

        content = path.read_bytes()
        return SourceDocument(
            source_system=self.source_system,
            source_capability=SOURCE_CAPABILITY,
            document_id=document_id,
            filename=path.name,
            media_type=_MEDIA_TYPES[suffix],
            content=content,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )
