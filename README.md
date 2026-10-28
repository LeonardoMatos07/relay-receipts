# relay-receipts

Python Lambdas that turn what providers say about a message ("delivered", "bounced", "complaint") into delivery receipts in [Relay](https://github.com/LeonardoMatos07/relay).

```
Relay -> SES -> SNS -> SQS relay-ses-events -> [webhook-ses] -> SQS relay-receipts -> [relay-receipts] -> POST /v1/receipts
                         raw SES events        raw -> neutral                          general webhook      Relay updates status
                                                                                                         + suppression list
```

- **webhook-ses** (`relay_receipts.handlers.webhook_ses`): reads raw SES events, validates and normalizes them into a provider-neutral receipt, queues them.
- **relay-receipts** (`relay_receipts.handlers.relay_receipts`): the general webhook. It knows nothing about SES; it takes any neutral receipt, dedupes it in a DynamoDB ledger and posts it to Relay.

A new provider (Twilio, SendGrid, ...) only needs its own small translator that publishes to the same queue. See [ADR 0001](docs/adr/0001-sqs-between-lambdas.md).

## The receipt

[`schemas/receipt.schema.json`](schemas/receipt.schema.json): `id`, `provider`, `providerMessageId`, `relayMessageId`, `channel`, `recipient`, `type` (`sent | delivered | delayed | bounced | complained | rejected`), `permanent`, `reason`, `occurredAt`. The id is derived from the provider event, so the same event always gets the same id.

## Failure handling

- Both functions use SQS partial batch responses: only failed messages return to the queue; after 5 attempts they land in a DLQ with an alarm.
- Relay answers 404 (unknown message) or 400/422 (invalid): the receipt is dropped and logged, retrying would not help. Anything else (5xx, 429, network) is retried.
- Delivery to Relay is at-least-once; the ledger and Relay's unique receipt id make it effectively once.

## Logs

Both functions write one JSON line per fact to CloudWatch Logs (`/aws/lambda/relay-webhook-ses`
and `/aws/lambda/relay-receipts`, retention set in relay-terraform). Every line has `level`,
`msg`, `function` and the invocation's `request_id`; each receipt gets its own line with
`receipt_id`, `provider`, `provider_message_id`, `relay_message_id`, `channel`, `type`,
`permanent`, `reason`, `occurred_at` and a masked `recipient` (`a***@example.org`).

| `msg`                                                  | When                                                       |
| ------------------------------------------------------ | ---------------------------------------------------------- |
| `receipt queued` / `receipt not queued`                | webhook-ses queued (or failed to queue) a receipt          |
| `ses event ignored`                                    | an SES event type Relay does not track (`event_type`)      |
| `receipt delivered to relay` (`outcome`)               | Relay answered: `recorded` or `duplicate`                  |
| `receipt not usable by relay` (`outcome`)              | `unmatched` (unknown message) or `rejected`; kept, not retried |
| `relay unavailable, will retry`                        | Relay failed or was unreachable; the message goes back     |
| `receipt already handled`                              | the ledger saw it before (SQS redelivery)                  |
| `ses events processed` / `receipts processed`          | one summary per batch                                      |

Follow one message through both functions (CloudWatch Logs Insights, both log groups):

```
fields @timestamp, function, msg, type, outcome, reason
| filter relay_message_id = "01a182e5-1c44-79c5-a76d-d4dae779dd10"
| sort @timestamp asc
```

## Develop

```bash
uv sync
uv run pytest        # 70 tests, includes real captured SES events (tests/fixtures)
uv run ruff check . && uv run mypy
```

Handlers depend on small ports (`ports.py`), so tests run without AWS; `test_relay_client.py` talks to a real local HTTP server and `test_pipeline.py` runs the whole chain in memory.

Infrastructure (queues, Lambdas, IAM, alarms) is in [relay-terraform](https://github.com/LeonardoMatos07/relay-terraform).
