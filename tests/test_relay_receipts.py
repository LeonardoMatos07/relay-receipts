from __future__ import annotations

import json
from typing import Any

import pytest

from relay_receipts import ses
from relay_receipts.handlers.relay_receipts import process
from relay_receipts.ports import RelayOutcome
from tests.conftest import sqs_event
from tests.fakes import UNAVAILABLE, FakeLedger, FakeRelay


def body(load: Any, name: str = "ses_delivery") -> str:
    return json.dumps(ses.normalize(load(name))[0].to_json())


def test_delivers_a_receipt_to_relay_and_records_it(load: Any) -> None:
    ledger, relay = FakeLedger(), FakeRelay("recorded")

    result = process(sqs_event(body(load)), ledger, relay)

    assert result == {"batchItemFailures": []}
    assert len(relay.calls) == 1
    assert list(ledger.items.values()) == ["recorded"]


def test_a_receipt_seen_before_is_not_sent_again(load: Any) -> None:
    ledger, relay = FakeLedger(), FakeRelay()

    assert process(sqs_event(body(load), body(load)), ledger, relay) == {"batchItemFailures": []}
    assert len(relay.calls) == 1


def test_relay_down_puts_the_message_back_and_a_later_attempt_finishes(load: Any) -> None:
    ledger = FakeLedger()
    batch = sqs_event(body(load))

    first = process(batch, ledger, FakeRelay(UNAVAILABLE))
    assert first == {"batchItemFailures": [{"itemIdentifier": "sqs-0"}]}
    assert list(ledger.items.values()) == ["processing"]

    relay = FakeRelay("recorded")
    assert process(batch, ledger, relay) == {"batchItemFailures": []}
    assert len(relay.calls) == 1
    assert list(ledger.items.values()) == ["recorded"]


@pytest.mark.parametrize("outcome", ["unmatched", "rejected"])
def test_a_receipt_relay_cannot_use_is_acknowledged_and_kept(
    load: Any, outcome: RelayOutcome
) -> None:
    ledger = FakeLedger()

    assert process(sqs_event(body(load)), ledger, FakeRelay(outcome)) == {"batchItemFailures": []}
    assert list(ledger.items.values()) == [outcome]


def test_a_duplicate_answer_from_relay_is_a_success(load: Any) -> None:
    ledger = FakeLedger()

    assert process(sqs_event(body(load)), ledger, FakeRelay("duplicate")) == {
        "batchItemFailures": []
    }
    assert list(ledger.items.values()) == ["duplicate"]


def test_an_invalid_receipt_goes_to_the_dlq_path_without_touching_relay(load: Any) -> None:
    relay = FakeRelay()

    result = process(sqs_event(body(load), "{}", "not json"), FakeLedger(), relay)

    assert result == {
        "batchItemFailures": [{"itemIdentifier": "sqs-1"}, {"itemIdentifier": "sqs-2"}]
    }
    assert len(relay.calls) == 1


def test_one_failure_does_not_block_the_rest_of_the_batch(load: Any) -> None:
    batch = sqs_event(
        body(load, "ses_delivery"), body(load, "ses_bounce"), body(load, "ses_complaint")
    )
    relay = FakeRelay("recorded", UNAVAILABLE, "recorded")

    result = process(batch, FakeLedger(), relay)

    assert result == {"batchItemFailures": [{"itemIdentifier": "sqs-1"}]}
    assert len(relay.calls) == 3
