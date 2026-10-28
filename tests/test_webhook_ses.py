from __future__ import annotations

import json
from typing import Any

from relay_receipts.handlers.webhook_ses import process
from tests.conftest import sqs_event
from tests.fakes import FakeSink


def test_queues_one_receipt_per_ses_event(load: Any) -> None:
    sink = FakeSink()
    names = ("ses_send", "ses_delivery", "ses_bounce")
    event = sqs_event(*(json.dumps(load(name)) for name in names))

    assert process(event, sink) == {"batchItemFailures": []}
    assert [r.type for r in sink.sent] == ["sent", "delivered", "bounced"]


def test_ignored_events_are_acknowledged_without_queueing(load: Any) -> None:
    sink = FakeSink()
    event = load("ses_send")
    event["eventType"] = "Open"

    assert process(sqs_event(json.dumps(event)), sink) == {"batchItemFailures": []}
    assert sink.sent == []


def test_an_unreadable_message_fails_alone(load: Any) -> None:
    sink = FakeSink()
    batch = sqs_event(
        json.dumps(load("ses_delivery")), "not json", json.dumps(load("ses_complaint"))
    )

    result = process(batch, sink)

    assert result == {"batchItemFailures": [{"itemIdentifier": "sqs-1"}]}
    assert [r.type for r in sink.sent] == ["delivered", "complained"]


def test_a_message_that_could_not_be_queued_is_retried(load: Any) -> None:
    sink = FakeSink(fail={1})
    batch = sqs_event(json.dumps(load("ses_send")), json.dumps(load("ses_delivery")))

    assert process(batch, sink) == {"batchItemFailures": [{"itemIdentifier": "sqs-1"}]}


def test_a_record_with_several_receipts_fails_once(load: Any) -> None:
    bounce = load("ses_bounce")
    bounce["bounce"]["bouncedRecipients"].append({"emailAddress": "other@example.org"})
    sink = FakeSink(fail={0, 1})

    result = process(sqs_event(json.dumps(bounce)), sink)

    assert result == {"batchItemFailures": [{"itemIdentifier": "sqs-0"}]}


def test_an_empty_batch_is_fine() -> None:
    assert process({"Records": []}, FakeSink()) == {"batchItemFailures": []}
