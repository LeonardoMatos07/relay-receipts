"""Lambda `relay-receipts`: the general webhook that hands receipts to Relay.

Triggered by the receipts queue. It knows nothing about any provider: every provider's own
adapter (today `webhook-ses`) sends it the same receipt. It records each receipt in a ledger so a
receipt SQS delivers twice is handled once, then posts it to Relay, which is idempotent too.

What counts as a failure (the message goes back to the queue and, after the retries, to the DLQ):
Relay unreachable or answering 5xx or refusing the credentials, and receipts that are not valid.
What is acknowledged: delivered, already delivered, and receipts Relay cannot use (no such
message, or rejected as malformed), which are logged and kept in the ledger.
"""

from __future__ import annotations

import json
from typing import Any

from relay_receipts import logs
from relay_receipts.ports import Ledger, RelayGateway, RelayUnavailableError
from relay_receipts.receipt import InvalidReceiptError, Receipt


def process(event: dict[str, Any], ledger: Ledger, relay: RelayGateway) -> dict[str, Any]:
    failures: list[str] = []
    counts = {"recorded": 0, "duplicate": 0, "unmatched": 0, "rejected": 0, "skipped": 0}

    for record in event.get("Records", []):
        message_id = str(record.get("messageId", ""))
        try:
            receipt = Receipt.from_json(json.loads(record["body"]))
        except (InvalidReceiptError, json.JSONDecodeError, KeyError) as error:
            logs.error("invalid receipt", sqs_message=message_id, error=str(error))
            failures.append(message_id)
            continue

        fields = logs.receipt_fields(receipt)
        if ledger.begin(receipt) == "done":
            counts["skipped"] += 1
            logs.info("receipt already handled", sqs_message=message_id, **fields)
            continue
        try:
            outcome = relay.deliver(receipt)
        except RelayUnavailableError as error:
            logs.warning(
                "relay unavailable, will retry", sqs_message=message_id, error=str(error), **fields
            )
            failures.append(message_id)
            continue

        ledger.finish(receipt.id, outcome)
        counts[outcome] += 1
        if outcome in ("unmatched", "rejected"):
            logs.warning(
                "receipt not usable by relay", sqs_message=message_id, outcome=outcome, **fields
            )
        else:
            logs.info(
                "receipt delivered to relay", sqs_message=message_id, outcome=outcome, **fields
            )

    logs.info(
        "receipts processed", records=len(event.get("Records", [])), failed=len(failures), **counts
    )
    return {"batchItemFailures": [{"itemIdentifier": item} for item in failures]}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    from relay_receipts.aws import DynamoLedger
    from relay_receipts.relay import HttpRelay

    logs.bind(function="relay-receipts", request_id=getattr(context, "aws_request_id", None))
    return process(event, DynamoLedger.from_environment(), HttpRelay.from_environment())
