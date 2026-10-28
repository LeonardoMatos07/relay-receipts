"""Lambda `webhook-ses`: raw Amazon SES events in, provider-neutral receipts out.

Triggered by the SQS queue that SES feeds through SNS. For each message it turns the SES event
into receipts and queues them for `relay-receipts`. It reports failures per message
(`ReportBatchItemFailures`), so one bad message does not make SQS redeliver the whole batch.
"""

from __future__ import annotations

from typing import Any

from relay_receipts import logs, ses
from relay_receipts.ports import ReceiptSink
from relay_receipts.receipt import Receipt


def process(event: dict[str, Any], sink: ReceiptSink) -> dict[str, Any]:
    failures: list[str] = []
    pending: list[tuple[str, Receipt]] = []
    ignored = 0

    for record in event.get("Records", []):
        message_id = str(record.get("messageId", ""))
        try:
            receipts = ses.normalize(ses.unwrap(record["body"]))
        except (ses.InvalidSesEventError, KeyError) as error:
            # Not retried in place: after the queue's maxReceiveCount it lands in the DLQ.
            logs.error("unreadable SES event", sqs_message=message_id, error=str(error))
            failures.append(message_id)
            continue
        if not receipts:
            ignored += 1
            continue
        pending.extend((message_id, receipt) for receipt in receipts)

    failed_positions = sink.send([receipt for _, receipt in pending]) if pending else set()
    for position in failed_positions:
        message_id = pending[position][0]
        if message_id not in failures:
            failures.append(message_id)

    logs.info(
        "ses events processed",
        records=len(event.get("Records", [])),
        receipts=len(pending) - len(failed_positions),
        ignored=ignored,
        failed=len(failures),
    )
    return {"batchItemFailures": [{"itemIdentifier": item} for item in failures]}


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    from relay_receipts.aws import SqsReceiptSink

    return process(event, SqsReceiptSink.from_environment())
