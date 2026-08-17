"""Small, read-only service entry points for the A-share system."""

from .read_only_receipt_server import (
    READ_ONLY_RECEIPT_SERVICE_VERSION,
    ReadOnlyReceiptServiceError,
    create_read_only_receipt_server,
    serve_read_only_receipt,
)

__all__ = [
    "READ_ONLY_RECEIPT_SERVICE_VERSION",
    "ReadOnlyReceiptServiceError",
    "create_read_only_receipt_server",
    "serve_read_only_receipt",
]
