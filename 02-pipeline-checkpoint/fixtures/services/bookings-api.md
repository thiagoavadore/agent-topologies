# bookings-api

Owner: Team Rentals (rentals@harbourbikes.example)
Runtime: Python 3.12, FastAPI, 4 replicas behind the shared load balancer across two zones.

## What it does
Takes bike reservations from the mobile app and the website, holds a bike for 10 minutes, then charges the customer through payments-gateway.

## Endpoints
- `POST /v1/bookings` (public, called by the app and the website)
- `GET /v1/bookings/{id}` (public, authenticated with the customer's session token)
- `POST /internal/bookings/{id}/release` (internal only, mTLS)

Public endpoints accept any volume of requests per client; we scale out when the load balancer reports high CPU.

## Dependencies
```
fastapi==0.115.6
requests==2.32.3
sqlalchemy==2.0.36
```

## Payment call
```python
def charge(booking):
    return requests.post(PAYMENTS_URL + "/charge", json=booking.to_payment())
```

## Data
Reservations live in the shared Postgres cluster (daily snapshots, 30-day retention, restore tested in June).
