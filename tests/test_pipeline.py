"""The whole chain in memory: SES events -> webhook-ses -> queue -> relay-receipts -> Relay."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from relay_receipts.handlers import relay_receipts, webhook_ses
from relay_receipts.receipt import Receipt
from relay_receipts.relay import HttpRelay
from tests.conftest import sqs_event
from tests.fakes import FakeLedger
from tests.test_relay_client import FakeRelayServer


class InMemoryQueue:
    """Stands in for the receipts SQS queue: whatever webhook-ses sends, relay-receipts reads."""

    def __init__(self) -> None:
        self.bodies: list[str] = []

    def send(self, receipts: Sequence[Receipt]) -> set[int]:
        self.bodies.extend(json.dumps(r.to_json()) for r in receipts)
        return set()


def test_every_ses_event_reaches_relay_as_a_neutral_receipt(load: Any) -> None:
    queue = InMemoryQueue()
    names = ("ses_send", "ses_delivery", "ses_bounce", "ses_complaint")
    ses_batch = sqs_event(*(json.dumps(load(name)) for name in names))

    assert webhook_ses.process(ses_batch, queue) == {"batchItemFailures": []}
    assert len(queue.bodies) == 4

    with FakeRelayServer() as server:
        relay = HttpRelay(server.url, lambda: "key")
        result = relay_receipts.process(sqs_event(*queue.bodies), FakeLedger(), relay)

        assert result == {"batchItemFailures": []}
        assert [r["body"]["type"] for r in server.requests] == [
            "sent",
            "delivered",
            "bounced",
            "complained",
        ]
        assert all(r["body"]["provider"] == "ses" for r in server.requests)
        assert server.requests[2]["body"]["permanent"] is True


def test_a_redelivered_queue_message_reaches_relay_once(load: Any) -> None:
    queue = InMemoryQueue()
    webhook_ses.process(sqs_event(json.dumps(load("ses_delivery"))), queue)
    ledger = FakeLedger()

    with FakeRelayServer() as server:
        relay = HttpRelay(server.url, lambda: None)
        # SQS is at-least-once: the same message arrives in two invocations.
        relay_receipts.process(sqs_event(*queue.bodies), ledger, relay)
        relay_receipts.process(sqs_event(*queue.bodies), ledger, relay)

        assert len(server.requests) == 1
