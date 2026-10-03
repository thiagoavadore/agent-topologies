# payments-gateway

Owner: Team Money (money@harbourbikes.example)
Runtime: Go 1.23, 3 replicas across two zones.

## What it does
Wraps the card processor. Every charge and refund at Harbour Bikes goes through here.

## Configuration
```yaml
processor:
  base_url: https://api.cardprocessor.example/v2
  merchant_id: HB-221-NL
  api_key: cp_live_9f2c8e71d4a04b6fa3e1c55d0b8a2f17
  timeout_ms: 4000
  retries: 2
```

## Limits
Charges are limited to 20 requests per second per calling service, enforced at the gateway.

## Dependencies
```
github.com/stripe-like/sdk v1.14.2
go.uber.org/zap v1.27.0
```

## Data
Idempotency keys in Redis (replicated, AOF on). Ledger rows in the shared Postgres cluster (daily snapshots, 30-day retention).
