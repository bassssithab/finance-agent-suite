import base64

import pytest

from connectors import (
    ContentTooLargeError,
    UnsupportedMediaTypeError,
    build_content_block,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-invoice-image-payload"
PDF_BYTES = b"%PDF-1.4\n" + b"fake-contract-payload"


@pytest.mark.parametrize(
    "media_type", ["image/png", "image/jpeg", "image/gif", "image/webp"]
)
def test_image_block_shape_unchanged(media_type):
    block = build_content_block(PNG_BYTES, media_type)

    assert block == {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.standard_b64encode(PNG_BYTES).decode("ascii"),
        },
    }


def test_pdf_block_shape():
    block = build_content_block(PDF_BYTES, "application/pdf")

    assert block == {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": base64.standard_b64encode(PDF_BYTES).decode("ascii"),
        },
    }


def test_image_base64_round_trips_to_original_bytes():
    block = build_content_block(PNG_BYTES, "image/png")

    assert base64.standard_b64decode(block["source"]["data"]) == PNG_BYTES


def test_pdf_base64_round_trips_to_original_bytes():
    block = build_content_block(PDF_BYTES, "application/pdf")

    assert base64.standard_b64decode(block["source"]["data"]) == PDF_BYTES


def test_unsupported_media_type_raises():
    with pytest.raises(UnsupportedMediaTypeError):
        build_content_block(b"hello", "text/plain")


def test_oversize_image_raises_with_default_cap():
    content = b"x" * (5 * 1024 * 1024 + 1)

    with pytest.raises(ContentTooLargeError):
        build_content_block(content, "image/png")


def test_image_at_exactly_default_cap_passes():
    content = b"x" * (5 * 1024 * 1024)

    build_content_block(content, "image/png")  # does not raise


def test_oversize_pdf_raises_with_default_cap():
    content = b"x" * (10 * 1024 * 1024 + 1)

    with pytest.raises(ContentTooLargeError):
        build_content_block(content, "application/pdf")


def test_pdf_at_exactly_default_cap_passes():
    content = b"x" * (10 * 1024 * 1024)

    build_content_block(content, "application/pdf")  # does not raise


def test_oversize_with_custom_cap_raises():
    with pytest.raises(ContentTooLargeError):
        build_content_block(b"x" * 101, "image/png", max_image_bytes=100)


def test_at_custom_cap_passes():
    build_content_block(b"x" * 100, "image/png", max_image_bytes=100)  # does not raise


def test_custom_pdf_cap_is_independent_of_image_cap():
    # A PDF just over the default image cap but under a custom (lower) PDF
    # cap's complement should still be governed by max_pdf_bytes, not
    # max_image_bytes.
    content = b"x" * (6 * 1024 * 1024)

    build_content_block(content, "application/pdf")  # under the 10 MiB PDF cap
    with pytest.raises(ContentTooLargeError):
        build_content_block(content, "application/pdf", max_pdf_bytes=1024)


def test_oversize_unsupported_type_raises_unsupported_not_too_large():
    # Media type is checked before size — an oversized file of an unknown
    # type should get the more useful UnsupportedMediaTypeError.
    content = b"x" * (50 * 1024 * 1024)

    with pytest.raises(UnsupportedMediaTypeError):
        build_content_block(content, "text/plain")
