from __future__ import annotations

import json
from typing import Any

from relay_receipts import ses
from relay_receipts.aws import DynamoLedger, SqsReceiptSink
from relay_receipts.receipt import Receipt


class FakeSqs:
    def __init__(self, failed_ids: set[str] | None = None, explode: bool = False) -> None:
        self.failed_ids = failed_ids or set()
        self.explode = explode
        self.calls: list[list[dict[str, Any]]] = []

    def send_message_batch(self, QueueUrl: str, Entries: list[dict[str, Any]]) -> dict[str, Any]:
        if self.explode:
            raise RuntimeError("throttled")
        self.calls.append(Entries)
        failed = [
            {"Id": e["Id"], "Code": "InternalError", "Message": "x"}
            for e in Entries
            if e["Id"] in self.failed_ids
        ]
        return {"Failed": failed}


def receipts(load: Any, count: int) -> list[Receipt]:
    base = ses.normalize(load("ses_delivery"))[0]
    return [
        Receipt(**{**base.__dict__, "id": f"ses:{index}", "recipient": f"u{index}@example.org"})
        for index in range(count)
    ]


def test_sends_in_batches_of_ten_with_attributes(load: Any) -> None:
    client = FakeSqs()

    failed = SqsReceiptSink(client, "https://queue").send(receipts(load, 23))

    assert failed == set()
    assert [len(call) for call in client.calls] == [10, 10, 3]
    first = client.calls[0][0]
    assert json.loads(first["MessageBody"])["id"] == "ses:0"
    assert first["MessageAttributes"]["provider"]["StringValue"] == "ses"
    assert first["MessageAttributes"]["type"]["StringValue"] == "delivered"


def test_reports_the_positions_that_failed(load: Any) -> None:
    client = FakeSqs(failed_ids={"3", "12"})
    assert SqsReceiptSink(client, "https://queue").send(receipts(load, 15)) == {3, 12}


def test_a_failed_call_fails_the_whole_chunk(load: Any) -> None:
    assert SqsReceiptSink(FakeSqs(explode=True), "q").send(receipts(load, 3)) == {0, 1, 2}


class ConditionalCheckFailedError(Exception):
    pass


class FakeTable:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}
        outer = self

        class Exceptions:
            ConditionalCheckFailedException = ConditionalCheckFailedError

        class Client:
            exceptions = Exceptions()

        class Meta:
            client = Client()

        self.meta = Meta()
        self.outer = outer

    def put_item(self, Item: dict[str, Any], ConditionExpression: str) -> None:
        assert ConditionExpression == "attribute_not_exists(id)"
        if Item["id"] in self.items:
            raise ConditionalCheckFailedError
        self.items[Item["id"]] = Item

    def get_item(self, Key: dict[str, str], ConsistentRead: bool) -> dict[str, Any]:
        assert ConsistentRead
        item = self.items.get(Key["id"])
        return {"Item": item} if item else {}

    def update_item(
        self, Key: dict[str, str], ExpressionAttributeValues: dict[str, Any], **_: Any
    ) -> None:
        self.items[Key["id"]]["status"] = ExpressionAttributeValues[":outcome"]


def test_the_ledger_tells_first_attempts_retries_and_finished_receipts_apart(load: Any) -> None:
    receipt = receipts(load, 1)[0]
    ledger = DynamoLedger(FakeTable(), now=lambda: 1_000)

    assert ledger.begin(receipt) == "new"
    assert ledger.begin(receipt) == "retry"
    ledger.finish(receipt.id, "recorded")
    assert ledger.begin(receipt) == "done"


def test_the_ledger_item_expires(load: Any) -> None:
    table = FakeTable()
    receipt = receipts(load, 1)[0]
    DynamoLedger(table, now=lambda: 1_000).begin(receipt)

    item = table.items[receipt.id]
    assert item["ttl"] == 1_000 + 30 * 86_400
    assert item["status"] == "processing"
    assert item["relayMessageId"] == receipt.relay_message_id
