"""What the handlers need from the outside world, so tests can replace it and the AWS clients
(`aws.py`) stay thin."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Protocol

from relay_receipts.receipt import Receipt

LedgerState = Literal["new", "retry", "done"]
RelayOutcome = Literal["recorded", "duplicate", "unmatched", "rejected"]


class RelayUnavailableError(Exception):
    """Relay did not accept the receipt for a reason that may pass: try again later."""


class ReceiptSink(Protocol):
    def send(self, receipts: Sequence[Receipt]) -> set[int]:
        """Queues the receipts and returns the positions that could not be queued."""
        ...


class Ledger(Protocol):
    def begin(self, receipt: Receipt) -> LedgerState:
        """`new` the first time, `retry` if an attempt did not finish, `done` once finished."""
        ...

    def finish(self, receipt_id: str, outcome: str) -> None: ...


class RelayGateway(Protocol):
    def deliver(self, receipt: Receipt) -> RelayOutcome:
        """Raises RelayUnavailableError for anything worth retrying."""
        ...
