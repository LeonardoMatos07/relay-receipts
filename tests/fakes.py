from __future__ import annotations

from collections.abc import Sequence

from relay_receipts.ports import LedgerState, RelayOutcome, RelayUnavailableError
from relay_receipts.receipt import Receipt


class FakeSink:
    def __init__(self, fail: set[int] | None = None) -> None:
        self.fail = fail or set()
        self.sent: list[Receipt] = []

    def send(self, receipts: Sequence[Receipt]) -> set[int]:
        self.sent.extend(r for i, r in enumerate(receipts) if i not in self.fail)
        return {i for i in self.fail if i < len(receipts)}


class FakeLedger:
    def __init__(self) -> None:
        self.items: dict[str, str] = {}

    def begin(self, receipt: Receipt) -> LedgerState:
        status = self.items.get(receipt.id)
        if status is None:
            self.items[receipt.id] = "processing"
            return "new"
        return "retry" if status == "processing" else "done"

    def finish(self, receipt_id: str, outcome: str) -> None:
        self.items[receipt_id] = outcome


class FakeRelay:
    def __init__(self, *answers: RelayOutcome | Exception) -> None:
        self.answers = list(answers)
        self.calls: list[Receipt] = []

    def deliver(self, receipt: Receipt) -> RelayOutcome:
        self.calls.append(receipt)
        answer = self.answers.pop(0) if self.answers else "recorded"
        if isinstance(answer, Exception):
            raise answer
        return answer


UNAVAILABLE = RelayUnavailableError("relay answered 503")
