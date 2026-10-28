"""One JSON line per fact, which CloudWatch Logs Insights can query without parsing."""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

_logger = logging.getLogger("relay_receipts")
if not _logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(handler)
    _logger.setLevel(logging.INFO)
    _logger.propagate = False


def log(level: int, message: str, **fields: Any) -> None:
    _logger.log(level, json.dumps({"msg": message, **fields}, default=str))


def info(message: str, **fields: Any) -> None:
    log(logging.INFO, message, **fields)


def warning(message: str, **fields: Any) -> None:
    log(logging.WARNING, message, **fields)


def error(message: str, **fields: Any) -> None:
    log(logging.ERROR, message, **fields)
