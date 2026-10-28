from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from relay_receipts import logs
from relay_receipts.handlers import relay_receipts, webhook_ses
from tests.conftest import sqs_event
from tests.fakes import FakeLedger, FakeRelay, FakeSink


class Lines:
    def __init__(self, caplog: pytest.LogCaptureFixture) -> None:
        self._caplog = caplog

    def get(self, msg: str) -> dict[str, Any]:
        parsed = (json.loads(record.getMessage()) for record in self._caplog.records)
        return next(line for line in parsed if line["msg"] == msg)


@pytest.fixture
def lines(caplog: pytest.LogCaptureFixture) -> Lines:
    logs._logger.propagate = True
    caplog.set_level(logging.INFO, logger="relay_receipts")
    return Lines(caplog)


def test_webhook_ses_logs_every_receipt_with_the_fields_to_follow_a_message(
    load: Any, lines: Lines
) -> None:
    logs.bind(function="webhook-ses", request_id="req-1")
    webhook_ses.process(sqs_event(json.dumps(load("ses_bounce"))), FakeSink())

    line = lines.get("receipt queued")
    assert line["level"] == "INFO"
    assert line["function"] == "webhook-ses"
    assert line["request_id"] == "req-1"
    assert line["type"] == "bounced"
    assert line["permanent"] is True
    assert line["provider"] == "ses"
    assert line["receipt_id"].startswith("ses:")
    assert line["relay_message_id"]
    assert line["provider_message_id"]
    assert "@" in line["recipient"]
    assert "***" in line["recipient"]
    assert lines.get("ses events processed")["receipts"] == 1


def test_webhook_ses_logs_what_it_ignores(load: Any, lines: Lines) -> None:
    event = load("ses_send")
    event["eventType"] = "Open"
    webhook_ses.process(sqs_event(json.dumps(event)), FakeSink())

    assert lines.get("ses event ignored")["event_type"] == "Open"


def test_relay_receipts_logs_the_outcome_of_each_receipt(load: Any, lines: Lines) -> None:
    body = json.dumps(_receipt_json(load))
    ledger = FakeLedger()
    relay_receipts.process(sqs_event(body), ledger, FakeRelay("recorded"))
    relay_receipts.process(sqs_event(body), ledger, FakeRelay("recorded"))

    delivered = lines.get("receipt delivered to relay")
    assert delivered["outcome"] == "recorded"
    assert delivered["relay_message_id"]
    assert lines.get("receipt already handled")["receipt_id"] == delivered["receipt_id"]


def test_recipients_are_masked() -> None:
    assert logs.mask_recipient("ana.silva@example.org") == "a***@example.org"
    assert logs.mask_recipient("+5571999991234") == "***1234"
    assert logs.mask_recipient("x") == "***"


def _receipt_json(load: Any) -> dict[str, Any]:
    from relay_receipts import ses

    return ses.normalize(load("ses_delivery"))[0].to_json()
