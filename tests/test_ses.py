from __future__ import annotations

import json
from typing import Any

import jsonschema
import pytest

from relay_receipts import ses
from relay_receipts.receipt import Receipt

SCHEMA = json.loads(
    (__import__("pathlib").Path(__file__).parents[1] / "schemas" / "receipt.schema.json").read_text(
        encoding="utf8"
    )
)


def only(event: dict[str, Any]) -> Receipt:
    receipts = ses.normalize(event)
    assert len(receipts) == 1
    return receipts[0]


def test_a_real_send_event(load: Any) -> None:
    receipt = only(load("ses_send"))
    assert receipt.type == "sent"
    assert receipt.provider == "ses"
    assert receipt.channel == "email"
    assert receipt.provider_message_id is not None
    assert receipt.relay_message_id is not None


def test_a_real_delivery_event(load: Any) -> None:
    event = load("ses_delivery")
    receipt = only(event)
    assert receipt.type == "delivered"
    assert receipt.recipient == "ana@example.org"
    assert receipt.occurred_at.isoformat() == "2026-10-27T22:17:20.091000+00:00"
    assert receipt.relay_message_id == event["mail"]["tags"]["relay_message_id"][0]
    assert receipt.permanent is None


def test_a_real_hard_bounce(load: Any) -> None:
    receipt = only(load("ses_bounce"))
    assert receipt.type == "bounced"
    assert receipt.permanent is True
    assert receipt.recipient == "bounce@simulator.amazonses.com"
    assert receipt.reason is not None
    assert "user unknown" in receipt.reason


def test_a_real_complaint(load: Any) -> None:
    receipt = only(load("ses_complaint"))
    assert receipt.type == "complained"
    assert receipt.reason == "abuse"


def test_a_soft_bounce_is_not_permanent(load: Any) -> None:
    event = load("ses_bounce")
    event["bounce"]["bounceType"] = "Transient"
    event["bounce"]["bounceSubType"] = "MailboxFull"
    assert only(event).permanent is False


def test_a_delivery_delay(load: Any) -> None:
    event = load("ses_delivery")
    event["eventType"] = "DeliveryDelay"
    event["deliveryDelay"] = {
        "timestamp": "2026-10-27T22:20:00.000Z",
        "delayType": "TransientCommunicationFailure",
        "delayedRecipients": [
            {"emailAddress": "ana@example.org", "diagnosticCode": "smtp; 451 try later"}
        ],
    }
    receipt = only(event)
    assert receipt.type == "delayed"
    assert receipt.reason == "smtp; 451 try later"


def test_a_reject_applies_to_every_destination(load: Any) -> None:
    event = load("ses_send")
    event["eventType"] = "Reject"
    event["reject"] = {"reason": "Bad content"}
    event["mail"]["destination"] = ["a@example.org", "b@example.org"]
    receipts = ses.normalize(event)
    assert [r.recipient for r in receipts] == ["a@example.org", "b@example.org"]
    assert {r.type for r in receipts} == {"rejected"}
    assert receipts[0].reason == "Bad content"
    assert receipts[0].id != receipts[1].id


def test_one_receipt_per_recipient_of_a_bounce(load: Any) -> None:
    event = load("ses_bounce")
    event["bounce"]["bouncedRecipients"].append({"emailAddress": "other@example.org"})
    assert len(ses.normalize(event)) == 2


def test_the_id_is_stable_for_the_same_event_and_differs_between_events(load: Any) -> None:
    assert only(load("ses_delivery")).id == only(load("ses_delivery")).id
    assert only(load("ses_delivery")).id != only(load("ses_send")).id


def test_events_that_say_nothing_about_delivery_are_ignored(load: Any) -> None:
    for kind in ("Open", "Click", "Rendering Failure", "Subscription"):
        event = load("ses_send")
        event["eventType"] = kind
        assert ses.normalize(event) == []


def test_a_message_without_the_relay_tag_is_still_matchable_by_provider_id(load: Any) -> None:
    event = load("ses_delivery")
    del event["mail"]["tags"]["relay_message_id"]
    receipt = only(event)
    assert receipt.relay_message_id is None
    assert receipt.provider_message_id == event["mail"]["messageId"]


def test_a_tag_that_is_not_a_uuid_is_dropped(load: Any) -> None:
    event = load("ses_delivery")
    event["mail"]["tags"]["relay_message_id"] = ["not-a-uuid"]
    assert only(event).relay_message_id is None


def test_the_sns_envelope_is_removed(load: Any) -> None:
    inner = json.dumps(load("ses_delivery"))
    wrapped = json.dumps({"Type": "Notification", "MessageId": "x", "Message": inner})
    assert ses.normalize(ses.unwrap(wrapped))[0].type == "delivered"


@pytest.mark.parametrize(
    "body",
    ["not json", "[]", json.dumps({"mail": {}}), json.dumps({"eventType": "Delivery"})],
)
def test_malformed_events_are_refused(body: str) -> None:
    with pytest.raises(ses.InvalidSesEventError):
        ses.normalize(ses.unwrap(body))


@pytest.mark.parametrize("name", ["ses_send", "ses_delivery", "ses_bounce", "ses_complaint"])
def test_every_receipt_matches_the_published_schema(load: Any, name: str) -> None:
    for receipt in ses.normalize(load(name)):
        jsonschema.validate(receipt.to_json(), SCHEMA)
        assert Receipt.from_json(receipt.to_json()) == receipt
