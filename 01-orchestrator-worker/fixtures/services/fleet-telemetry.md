# fleet-telemetry

Owner:
Runtime: Rust, one ingest process on a single VM (`telemetry-01`).

## What it does
Every bike reports GPS position and battery level every 30 seconds over MQTT. The ingest process subscribes to the broker, writes positions to TimescaleDB, and raises alerts for bikes that leave the city boundary.

## Topology
- One Mosquitto broker on `telemetry-01`, no standby.
- Ingest process on the same VM, restarted by systemd if it crashes.
- If the VM is down, bikes buffer 15 minutes of readings, then drop them.

## Dependencies
```
rumqttc = "0.24.0"
tokio = "1.41.1"
sqlx = "0.8.2"
```

## Data
TimescaleDB on managed Postgres with point-in-time recovery enabled (7 days).

## Notes
The original author moved to another company in March. Pages for this service go to the general on-call rotation.
