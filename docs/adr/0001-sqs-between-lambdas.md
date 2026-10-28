# 0001. SQS, not API Gateway, between the two Lambdas

## Status
Accepted

## Context
`webhook-ses` produces neutral receipts and `relay-receipts` must receive them. Two options: webhook-ses calls relay-receipts through API Gateway (HTTP), or it publishes to an SQS queue that triggers relay-receipts.

## Decision
SQS queue.

## Why
- **Buffering.** Relay may be down or unreachable; receipts wait in the queue (14 days) instead of being lost or failing the caller.
- **Retries and DLQ for free.** Visibility timeout, redelivery, partial batch failure and a dead-letter queue replace code we would write for an HTTP call.
- **Backpressure.** The event source mapping caps concurrency, so a burst of SES events never becomes a burst against Relay.
- **No public endpoint** to authenticate, throttle or attack.
- **Cost.** SQS plus Lambda polling is cheaper than API Gateway requests at this scale.

## Consequences
- Delivery is at-least-once, so relay-receipts dedupes with a DynamoDB ledger and Relay ignores a repeated receipt id.
- Order is not guaranteed; Relay's status only moves forward (ranked), so a late "sent" cannot undo "delivered".
- API Gateway stays the right tool for providers that push to us (Twilio, ...): its handler would validate the signature and publish to this same queue.
