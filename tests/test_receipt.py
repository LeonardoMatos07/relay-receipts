from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from relay_receipts.receipt import InvalidReceiptError, Receipt

VALID: dict[str, Any] = {
    "schema": 1,
    "id": "ses:abc",
    "provider": "ses",
    "providerMessageId": "0101abc",
    "relayMessageId": "01a17f6c-6929-761e-8307-76cdee1dc7fc",
    "channel": "email",
    "recipient": "ana@example.org",
    "type": "delivered",
    "occurredAt": "2026-10-27T19:12:54.994-03:00",
}


def test_round_trip_normalizes_the_date_to_utc() -> None:
    receipt = Receipt.from_json(VALID)
    assert receipt.occurred_at == datetime(2026, 10, 27, 22, 12, 54, 994000, tzinfo=UTC)
    assert receipt.to_json()["occurredAt"] == "2026-10-27T22:12:54.994Z"
    assert Receipt.from_json(receipt.to_json()) == receipt


def test_optional_fields_are_left_out_of_the_json() -> None:
    body = Receipt.from_json({**VALID, "relayMessageId": None}).to_json()
    assert "relayMessageId" not in body
    assert "permanent" not in body


def test_every_problem_is_reported_at_once() -> None:
    with pytest.raises(InvalidReceiptError) as raised:
        Receipt.from_json(
            {"schema": 2, "type": "opened", "channel": "fax", "occurredAt": "yesterday"}
        )
    message = str(raised.value)
    for fragment in (
        "schema must be 1",
        "id is required",
        "type must be one of",
        "channel must be",
    ):
        assert fragment in message


@pytest.mark.parametrize(
    "change",
    [
        {"relayMessageId": "abc"},
        {"permanent": "yes"},
        {"recipient": ""},
        {"id": "x" * 600},
        {"occurredAt": 12},
    ],
)
def test_invalid_values_are_refused(change: dict[str, Any]) -> None:
    with pytest.raises(InvalidReceiptError):
        Receipt.from_json({**VALID, **change})


def test_a_receipt_needs_some_way_to_find_the_message() -> None:
    body = {k: v for k, v in VALID.items() if k not in ("relayMessageId", "providerMessageId")}
    with pytest.raises(InvalidReceiptError, match="relayMessageId or providerMessageId"):
        Receipt.from_json(body)


@pytest.mark.parametrize("raw", [None, "x", [], 3])
def test_only_objects_are_receipts(raw: object) -> None:
    with pytest.raises(InvalidReceiptError):
        Receipt.from_json(raw)
