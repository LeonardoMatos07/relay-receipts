"""The AWS side of the ports: SQS to queue receipts, DynamoDB as the ledger."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Sequence
from typing import Any

from relay_receipts import logs
from relay_receipts.ports import LedgerState
from relay_receipts.receipt import Receipt

SQS_BATCH_LIMIT = 10
LEDGER_TTL_DAYS = 30


class SqsReceiptSink:
    def __init__(self, client: Any, queue_url: str) -> None:
        self._client = client
        self._queue_url = queue_url

    @classmethod
    def from_environment(cls) -> SqsReceiptSink:
        import boto3

        return cls(boto3.client("sqs"), os.environ["RECEIPTS_QUEUE_URL"])

    def send(self, receipts: Sequence[Receipt]) -> set[int]:
        failed: set[int] = set()
        for start in range(0, len(receipts), SQS_BATCH_LIMIT):
            chunk = receipts[start : start + SQS_BATCH_LIMIT]
            entries = [
                {
                    "Id": str(start + offset),
                    "MessageBody": json.dumps(receipt.to_json()),
                    "MessageAttributes": {
                        "provider": {"DataType": "String", "StringValue": receipt.provider},
                        "type": {"DataType": "String", "StringValue": receipt.type},
                    },
                }
                for offset, receipt in enumerate(chunk)
            ]
            try:
                response = self._client.send_message_batch(
                    QueueUrl=self._queue_url, Entries=entries
                )
            except Exception as error:  # the whole call failed: every entry is retried
                logs.error("send_message_batch failed", error=str(error))
                failed.update(range(start, start + len(chunk)))
                continue
            for item in response.get("Failed", []):
                logs.error("receipt not queued", code=item.get("Code"), detail=item.get("Message"))
                failed.add(int(item["Id"]))
        return failed


class DynamoLedger:
    """One item per receipt id. A conditional write decides who handles a receipt first."""

    def __init__(self, table: Any, now: Any = time.time) -> None:
        self._table = table
        self._now = now

    @classmethod
    def from_environment(cls) -> DynamoLedger:
        import boto3

        return cls(boto3.resource("dynamodb").Table(os.environ["LEDGER_TABLE"]))

    def begin(self, receipt: Receipt) -> LedgerState:
        now = int(self._now())
        try:
            self._table.put_item(
                Item={
                    "id": receipt.id,
                    "status": "processing",
                    "provider": receipt.provider,
                    "type": receipt.type,
                    "relayMessageId": receipt.relay_message_id,
                    "createdAt": now,
                    "ttl": now + LEDGER_TTL_DAYS * 86_400,
                },
                ConditionExpression="attribute_not_exists(id)",
            )
            return "new"
        except self._table.meta.client.exceptions.ConditionalCheckFailedException:
            current = self._table.get_item(Key={"id": receipt.id}, ConsistentRead=True).get(
                "Item", {}
            )
            return "retry" if current.get("status") == "processing" else "done"

    def finish(self, receipt_id: str, outcome: str) -> None:
        self._table.update_item(
            Key={"id": receipt_id},
            UpdateExpression="SET #s = :outcome, finishedAt = :now",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":outcome": outcome, ":now": int(self._now())},
        )
