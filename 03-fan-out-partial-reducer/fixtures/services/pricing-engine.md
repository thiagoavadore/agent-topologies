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
Calls demand-model with the platform default timeout (`http.default_timeout` in `platform.yaml`, 300 ms today) and falls back to base prices on timeout. bookings-api gives pricing 400 ms in total, so a demand-model wait above 300 ms makes the whole booking fail.

## Data
Stateless. Price tables are loaded from the config service at startup.
