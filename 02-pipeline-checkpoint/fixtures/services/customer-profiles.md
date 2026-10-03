# customer-profiles

Owner: Team Identity (identity@harbourbikes.example)
Runtime: Kotlin, Spring Boot, 3 replicas across two zones.

## What it does
Stores customer accounts: name, email, phone, driving-licence check result, saved payment method token.

## Endpoints
- `GET /internal/profiles/{id}` (internal only, mTLS)
- `PATCH /internal/profiles/{id}` (internal only, mTLS)

## Dependencies
```
org.springframework.boot:spring-boot-starter-web:3.3.5
org.postgresql:postgresql:42.7.4
```

## Data
Own Postgres instance `profiles-db` on a VM we manage ourselves (not the shared cluster). The data directory is on the VM's local disk. No snapshot schedule or dump job is configured yet; it was on the migration plan for Q3.

## Timeouts
All outbound calls go through the shared client with a 2 s connect and 5 s read timeout.
