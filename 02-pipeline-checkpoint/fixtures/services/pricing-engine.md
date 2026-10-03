# pricing-engine

Owner: Team Rentals (rentals@harbourbikes.example)
Runtime: Node.js 22, 2 replicas across two zones.

## What it does
Computes the price of a rental from duration, bike type, zone and demand. bookings-api calls it before every reservation.

## package.json (excerpt)
```json
{
  "dependencies": {
    "express": "latest",
    "decimal.js": "*",
    "demand-model-client": "^2.0.0",
    "pino": "9.5.0"
  }
}
```
No lockfile is committed; the Docker build runs `npm install` on every image build.

## Calls
Calls demand-model with a 300 ms timeout and falls back to base prices on timeout.

## Data
Stateless. Price tables are loaded from the config service at startup.
