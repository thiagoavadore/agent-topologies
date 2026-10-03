# Checker rules for approval

The checker (`fixcheck.py`) reads the merged cards and the merged `platform.yaml` and decides, with no model involved, which of the 12 planted risks are fixed and which of two card needs broke. Tests: `tests/test_fixcheck.py` runs every rule against the unchanged repo (nothing fixed, nothing broken), against one hand-written fix per risk (only that risk flips), against a fully fixed repo (12 of 12) and against broken hubs.

Conventions the workers are told in their prompt, so the rules can rely on them:

- A setting with a hub key goes in `platform.yaml` and the card refers to it by key name (for example `http.default_timeout`).
- A card differs from the hub for itself with one line: `Override: <key> = <value>`. Prose such as "overridden to 200 ms" is not read as an override.

Where a rule looks at a section (`## Payment call` and so on) and a worker renamed or removed that heading, the rule reads the whole card instead.

## Planted risks: "fixed" means the merged repo has all of this

| Card and risk | Fixed when |
|---|---|
| bookings-api `missing_timeout` | The `## Payment call` section references `http.default_timeout` and the merged hub value is a duration above zero, or (without that reference) it has a `timeout=<something>` argument that is not `None`. |
| bookings-api `no_rate_limit` | The `## Endpoints` section states a rate (`10 requests per second`, `600/min`, `5 req/s`), or references `http.default_rate_limit` and the merged hub value is a rate. |
| payments-gateway `hardcoded_secret` | The card no longer contains `cp_live_` followed by 8 or more hex characters. |
| fleet-telemetry `single_point_of_failure` | The card says two or more instances, replicas, VMs, nodes, brokers, schedulers or hosts (digit 2 to 9, or two, three, four, up to two words in between), or a "second" VM, instance, scheduler, node or host, or standby or failover (not preceded by "no"); or it references `availability.min_instances` and the merged hub value is 2 or more. |
| fleet-telemetry `no_owner` | The `Owner:` line has text after it that is not `TBD`, `unknown`, `none` or `n/a`. |
| customer-profiles `missing_backup` | The `## Data` section pairs a schedule word (daily, nightly, hourly, weekly, every N hours or days) with snapshot, dump, backup, backed up or pg_dump, in either order within 40 characters; or it references `backup.policy` and the merged hub value is not `none`. |
| pricing-engine `unpinned_dependency` | Every version in the `package.json` block parses as JSON and is exact `N.N.N` (no `latest`, `*`, `^`); or the card references `dependencies.pinning` and the merged hub value is `exact` or `lockfile`. |
| admin-console `public_exposure` | The card has no `0.0.0.0/0`; or it says the console sits behind, requires, or goes via SSO, a VPN or single sign-on. |
| notifications `missing_timeout` | Same rule as bookings-api `missing_timeout`, on the `## Email sending` section. |
| notifications `unpinned_dependency` | Every line of the `requirements.txt` block is `name==version`; or the same hub-reference rule as pricing-engine. |
| maintenance-scheduler `single_point_of_failure` | Same rule as fleet-telemetry `single_point_of_failure`. |
| maintenance-scheduler `missing_backup` | Same rule as customer-profiles `missing_backup`, on its `## Data` section. |

## Planted card needs: "regressed" means the merged repo breaks one of these

| Need | Regressed when |
|---|---|
| pricing-engine's call to demand-model gives up within 300 ms | The effective timeout is above 300 ms or unparseable. Effective means the `Override: http.default_timeout = ...` line in `pricing-engine.md` if there is one, else the merged hub `http.default_timeout`. At the start the hub holds `300ms` and the card says it inherits it. |
| payments-gateway accepts 20 requests per second per calling service (bookings-api charges at that rate at peak) | The effective rate is below 20 per second or unparseable. Effective means the `Override: http.default_rate_limit = ...` line in `payments-gateway.md` if there is one, else the merged hub `http.default_rate_limit`; `none` counts as fine. At the start the hub holds `20/s` and the card says it inherits it. |

## Known looseness, for you to accept or tighten

- Fix rules look for evidence a fix is stated, not that it is correct code. `timeout=PAYMENTS_TIMEOUT` counts even if the variable is never defined.
- The payments need is expressed through a shared default, not through "the gateway's own callers": a global limit below 20/s is what breaks it.
- `public_exposure` is card-local, so a fix that only renames the CIDR still counts as fixed.
