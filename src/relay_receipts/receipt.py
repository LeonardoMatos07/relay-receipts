"""The provider-neutral delivery receipt that Relay understands.

Providers speak in their own formats. Everything in front of Relay turns each one into this
shape, so Relay never learns a provider's vocabulary. The JSON form is camelCase, as in Relay's
`POST /v1/receipts`, and is described by `schemas/receipt.schema.json`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

ReceiptType = Literal["sent", "delivered", "delayed", "bounced", "complained", "rejected"]
Channel = Literal["email", "sms", "whatsapp", "push"]

RECEIPT_TYPES: tuple[str, ...] = (
    "sent",
    "delivered",
    "delayed",
    "bounced",
    "complained",
    "rejected",
)
CHANNELS: tuple[str, ...] = ("email", "sms", "whatsapp", "push")
SCHEMA_VERSION = 1
MAX_TEXT = 512


class InvalidReceiptError(ValueError):
    """The payload is not a receipt. Retrying will not fix it."""


@dataclass(frozen=True)
class Receipt:
    id: str
    provider: str
    channel: Channel
    recipient: str
    type: ReceiptType
    occurred_at: datetime
    provider_message_id: str | None = None
    relay_message_id: str | None = None
    permanent: bool | None = None
    reason: str | None = None

    def to_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "schema": SCHEMA_VERSION,
            "id": self.id,
            "provider": self.provider,
            "channel": self.channel,
            "recipient": self.recipient,
            "type": self.type,
            "occurredAt": self.occurred_at.astimezone(UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
        }
        optional = {
            "providerMessageId": self.provider_message_id,
            "relayMessageId": self.relay_message_id,
            "permanent": self.permanent,
            "reason": self.reason,
        }
        body.update({key: value for key, value in optional.items() if value is not None})
        return body

    @classmethod
    def from_json(cls, raw: object) -> Receipt:
        if not isinstance(raw, dict):
            raise InvalidReceiptError("receipt must be a JSON object")
        errors: list[str] = []

        def text(name: str, *, required: bool) -> str | None:
            value = raw.get(name)
            if value is None:
                if required:
                    errors.append(f"{name} is required")
                return None
            if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT:
                errors.append(f"{name} must be a non-empty string of at most {MAX_TEXT} characters")
                return None
            return value.strip()

        if raw.get("schema") != SCHEMA_VERSION:
            errors.append(f"schema must be {SCHEMA_VERSION}")
        receipt_id = text("id", required=True)
        provider = text("provider", required=True)
        recipient = text("recipient", required=True)
        provider_message_id = text("providerMessageId", required=False)
        relay_message_id = text("relayMessageId", required=False)
        reason = text("reason", required=False)

        if relay_message_id is not None:
            try:
                uuid.UUID(relay_message_id)
            except ValueError:
                errors.append("relayMessageId must be a UUID")
        if relay_message_id is None and provider_message_id is None:
            errors.append("relayMessageId or providerMessageId is required")
        if raw.get("channel") not in CHANNELS:
            errors.append(f"channel must be one of: {', '.join(CHANNELS)}")
        if raw.get("type") not in RECEIPT_TYPES:
            errors.append(f"type must be one of: {', '.join(RECEIPT_TYPES)}")
        permanent = raw.get("permanent")
        if permanent is not None and not isinstance(permanent, bool):
            errors.append("permanent must be a boolean")
        occurred_at = _parse_time(raw.get("occurredAt"))
        if occurred_at is None:
            errors.append("occurredAt must be an ISO 8601 date")

        if (
            errors
            or occurred_at is None
            or receipt_id is None
            or provider is None
            or recipient is None
        ):
            raise InvalidReceiptError("; ".join(errors))
        return cls(
            id=receipt_id,
            provider=provider,
            channel=cast("Channel", raw["channel"]),
            recipient=recipient,
            type=cast("ReceiptType", raw["type"]),
            occurred_at=occurred_at,
            provider_message_id=provider_message_id,
            relay_message_id=relay_message_id,
            permanent=permanent,
            reason=reason,
        )


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
