"""Turns an Amazon SES event into delivery receipts.

SES publishes one JSON document per event to the configuration set's destination (here SNS, then
SQS). The shape depends on `eventType`; this module is the only place that knows it.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from relay_receipts.receipt import Receipt, ReceiptType

PROVIDER = "ses"
RELAY_TAG = "relay_message_id"

# SES eventType -> receipt type. Anything else (Open, Click, Subscription, Rendering Failure)
# says nothing about delivery and is ignored.
EVENT_TYPES: dict[str, ReceiptType] = {
    "Send": "sent",
    "Delivery": "delivered",
    "DeliveryDelay": "delayed",
    "Bounce": "bounced",
    "Complaint": "complained",
    "Reject": "rejected",
}

# eventType -> (key of the detail block, key listing the affected recipients inside it)
RECIPIENTS: dict[str, tuple[str, str]] = {
    "Delivery": ("delivery", "recipients"),
    "DeliveryDelay": ("deliveryDelay", "delayedRecipients"),
    "Bounce": ("bounce", "bouncedRecipients"),
    "Complaint": ("complaint", "complainedRecipients"),
}


class InvalidSesEventError(ValueError):
    """The document is not an SES event. Retrying will not fix it."""


def unwrap(body: str) -> dict[str, Any]:
    """Parses an SQS message body, removing the SNS envelope when SNS raw delivery is off."""
    try:
        document = json.loads(body)
    except json.JSONDecodeError as error:
        raise InvalidSesEventError(f"body is not JSON: {error.msg}") from error
    if not isinstance(document, dict):
        raise InvalidSesEventError("body must be a JSON object")
    if document.get("Type") == "Notification" and isinstance(document.get("Message"), str):
        return unwrap(document["Message"])
    return document


def normalize(event: dict[str, Any]) -> list[Receipt]:
    """One receipt per affected recipient; an empty list for events that are not about delivery."""
    event_type = event.get("eventType", event.get("notificationType"))
    if not isinstance(event_type, str):
        raise InvalidSesEventError("eventType is missing")
    receipt_type = EVENT_TYPES.get(event_type)
    if receipt_type is None:
        return []

    mail = event.get("mail")
    if not isinstance(mail, dict) or not isinstance(mail.get("messageId"), str):
        raise InvalidSesEventError("mail.messageId is missing")
    message_id: str = mail["messageId"]

    detail_key, recipients_key = RECIPIENTS.get(event_type, ("", ""))
    detail = event.get(detail_key, {}) if detail_key else {}
    if not isinstance(detail, dict):
        raise InvalidSesEventError(f"{detail_key} must be an object")
    recipients = _recipients(event_type, mail, detail, recipients_key)
    if not recipients:
        raise InvalidSesEventError("event has no recipient")

    occurred_at = _timestamp(detail.get("timestamp") or mail.get("timestamp"))
    relay_message_id = _relay_id(mail)
    permanent, reason = _outcome(event_type, event, detail)

    return [
        Receipt(
            id=_event_id(message_id, event_type, recipient, occurred_at),
            provider=PROVIDER,
            channel="email",
            recipient=recipient,
            type=receipt_type,
            occurred_at=occurred_at,
            provider_message_id=message_id,
            relay_message_id=relay_message_id,
            permanent=permanent,
            reason=reason,
        )
        for recipient in recipients
    ]


def _recipients(
    event_type: str, mail: dict[str, Any], detail: dict[str, Any], key: str
) -> list[str]:
    if not key:  # Send and Reject describe the whole message
        destination = mail.get("destination", [])
        return [item for item in destination if isinstance(item, str)]
    found = detail.get(key, [])
    addresses: list[str] = []
    for item in found if isinstance(found, list) else []:
        if isinstance(item, str):
            addresses.append(item)
        elif isinstance(item, dict) and isinstance(item.get("emailAddress"), str):
            addresses.append(item["emailAddress"])
    return addresses


def _timestamp(value: object) -> datetime:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
    raise InvalidSesEventError("event has no valid timestamp")


def _relay_id(mail: dict[str, Any]) -> str | None:
    tags = mail.get("tags")
    values = tags.get(RELAY_TAG) if isinstance(tags, dict) else None
    candidate = values[0] if isinstance(values, list) and values else None
    if not isinstance(candidate, str):
        return None
    try:
        uuid.UUID(candidate)
    except ValueError:
        return None
    return candidate


def _outcome(
    event_type: str, event: dict[str, Any], detail: dict[str, Any]
) -> tuple[bool | None, str | None]:
    if event_type == "Bounce":
        first = _first(detail.get("bouncedRecipients"))
        reason = first.get("diagnosticCode") or detail.get("bounceSubType")
        return detail.get("bounceType") == "Permanent", _short(reason)
    if event_type == "Complaint":
        return None, _short(detail.get("complaintFeedbackType"))
    if event_type == "DeliveryDelay":
        first = _first(detail.get("delayedRecipients"))
        return None, _short(first.get("diagnosticCode") or detail.get("delayType"))
    if event_type == "Reject":
        reject = event.get("reject")
        return None, _short(reject.get("reason") if isinstance(reject, dict) else None)
    return None, None


def _first(value: object) -> dict[str, Any]:
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return {}


def _short(value: object) -> str | None:
    return value[:500] if isinstance(value, str) and value else None


def _event_id(message_id: str, event_type: str, recipient: str, at: datetime) -> str:
    """Stable for one event, so a redelivery of the same SES event has the same id."""
    raw = f"{message_id}|{event_type}|{recipient.lower()}|{at.isoformat()}"
    return f"ses:{hashlib.sha256(raw.encode()).hexdigest()[:32]}"
