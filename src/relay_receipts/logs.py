"""One JSON line per fact, which CloudWatch Logs Insights can query without parsing.

Every line has `level` and `msg`, plus the invocation's `request_id` once a handler calls
`bind`. Per-receipt lines carry the same field names (`receipt_id`, `relay_message_id`, ...), so
one query follows a message from SES through both functions. Recipients are masked: the logs
are kept for weeks and should not become a list of addresses.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from relay_receipts.receipt import Receipt

_logger = logging.getLogger("relay_receipts")
if not _logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(handler)
    _logger.setLevel(logging.INFO)
    _logger.propagate = False

_bound: dict[str, Any] = {}


def bind(**fields: Any) -> None:
    """Adds fields to every line until the next call. Lambda reuses the process, so each
    invocation replaces them."""
    _bound.clear()
    _bound.update({key: value for key, value in fields.items() if value is not None})


def mask_recipient(recipient: str) -> str:
    local, at, domain = recipient.partition("@")
    if at:
        return f"{local[:1]}***@{domain}"
    return f"***{recipient[-4:]}" if len(recipient) > 4 else "***"


def receipt_fields(receipt: Receipt) -> dict[str, Any]:
    return {
        "receipt_id": receipt.id,
        "provider": receipt.provider,
        "provider_message_id": receipt.provider_message_id,
        "relay_message_id": receipt.relay_message_id,
        "channel": receipt.channel,
        "type": receipt.type,
        "permanent": receipt.permanent,
        "reason": receipt.reason,
        "occurred_at": receipt.occurred_at.isoformat(),
        "recipient": mask_recipient(receipt.recipient),
    }


def log(level: int, message: str, **fields: Any) -> None:
    line = {"level": logging.getLevelName(level), "msg": message, **_bound, **fields}
    _logger.log(level, json.dumps(line, default=str))


def info(message: str, **fields: Any) -> None:
    log(logging.INFO, message, **fields)


def warning(message: str, **fields: Any) -> None:
    log(logging.WARNING, message, **fields)


def error(message: str, **fields: Any) -> None:
    log(logging.ERROR, message, **fields)
