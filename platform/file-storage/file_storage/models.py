"""Data model for the prototype tenant-scoped file storage layer."""

from dataclasses import dataclass

__all__ = ["FileMetadata"]


@dataclass(frozen=True)
class FileMetadata:
    """What is tracked about one stored file. The bytes live on disk at
    <root>/<tenant_id>/<file_id>; this is everything else."""

    file_id: str
    tenant_id: str
    original_filename: str
    content_type: str
    size_bytes: int
    sha256: str
    stored_at: str  # ISO-8601 UTC
