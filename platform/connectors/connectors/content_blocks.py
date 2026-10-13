"""Build Claude content blocks from a `SourceDocument`'s bytes.

`FileDocumentConnector` already fetches PDFs the same way it fetches images
(`document_connector._MEDIA_TYPES` maps `.pdf` to `application/pdf`) — what's
been missing is turning those bytes into the right Messages API content
block. Images and PDFs use different block shapes (`type: "image"` vs.
`type: "document"`), so agents would otherwise have to special-case the
media type themselves, in every agent folder. This module is that one place.

Limits (verified against the current Anthropic API docs, platform.claude.com/
docs/en/build-with-claude/vision.md and pdf-support.md, 2026):
  - Images: 10 MB base64-encoded max per image on the direct Claude API (5 MB
    on Amazon Bedrock / Google Cloud). `DEFAULT_MAX_IMAGE_BYTES` is set to
    5 MiB — tighter than the API's own ceiling — so a connector-level reject
    happens well before a request-size problem would, with headroom to spare
    for base64's ~33% inflation and the rest of the request.
  - PDFs: 32 MB request size, 600 pages (100 pages on 200k-context models).
    `DEFAULT_MAX_PDF_BYTES` is set to 10 MiB, the same conservative default
    `platform/file-storage` uses — a PDF that size is already generous for
    a scanned invoice or contract, well under Anthropic's 32 MB ceiling.

Both caps are raw-byte caps checked before base64 encoding (base64 inflates
by ~4/3, so the actual request payload will be larger — the margin above is
deliberate), and both are overridable per call.
"""

import base64

from .document_connector import UnsupportedMediaTypeError

DEFAULT_MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MiB
DEFAULT_MAX_PDF_BYTES = 10 * 1024 * 1024  # 10 MiB

_IMAGE_MEDIA_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
_PDF_MEDIA_TYPE = "application/pdf"


class ContentTooLargeError(Exception):
    """Raised when content exceeds the applicable per-type size cap.

    Nothing is encoded or sent — the same "reject loudly, don't truncate or
    guess" stance as `UnsupportedMediaTypeError`.
    """


def build_content_block(
    content: bytes,
    media_type: str,
    *,
    max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
    max_pdf_bytes: int = DEFAULT_MAX_PDF_BYTES,
) -> dict:
    """Return the Messages API content block for `content`/`media_type`.

    Media type is checked before size, so an oversized file of a type we
    don't support at all raises `UnsupportedMediaTypeError` (the more useful
    error) rather than `ContentTooLargeError`.

    - image/png, image/jpeg, image/gif, image/webp -> an `image` block
      (identical shape to `ap_agent.extraction.build_image_block`).
    - application/pdf -> a `document` block.
    - anything else -> `UnsupportedMediaTypeError`.

    Raises `ContentTooLargeError` if `content` exceeds `max_image_bytes` (for
    an image) or `max_pdf_bytes` (for a PDF).
    """
    if media_type in _IMAGE_MEDIA_TYPES:
        block_type = "image"
        max_bytes = max_image_bytes
    elif media_type == _PDF_MEDIA_TYPE:
        block_type = "document"
        max_bytes = max_pdf_bytes
    else:
        raise UnsupportedMediaTypeError(f"unsupported media type {media_type!r}")

    if len(content) > max_bytes:
        raise ContentTooLargeError(
            f"{media_type} content is {len(content)} bytes, "
            f"over the {max_bytes}-byte limit"
        )

    return {
        "type": block_type,
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.standard_b64encode(content).decode("ascii"),
        },
    }
