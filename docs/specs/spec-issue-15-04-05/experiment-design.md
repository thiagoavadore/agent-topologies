# Experiment design: folders 04 and 05

Both folders use the Harbour Bikes world (`fixtures/harbour-bikes/`, 8 services, 11 planted risks, 5 needs) and score the final repo with `topologies.fixcheck.check()` unchanged. Ownership comes from each pristine `service.yaml` `owner.team`:

| Team | Services |
|---|---|
| Team Rentals | bookings-api, pricing-engine |
| Team Money | payments-gateway |
| Team Growth | notifications |
| Team Identity | customer-profiles |
| Team Fleet | maintenance-scheduler (fleet-telemetry has `owner: null`, the planted risk) |
| Team Ops Tools | admin-console |

The call graph that matters: bookings-api calls pricing-engine (waits 400 ms, so pricing needs at most 300 ms) and payments-gateway (charges at up to 20/s, so payments needs at least 20/s). Both regression needs are reached only through hub keys.

---

## 04-hierarchical

Org question: should the agent layers match the team layers?

### Tiers

| Tier | Model | Count | Sees | Returns |
|---|---|---|---|---|
| Worker | Sonnet 5.5 | 8, one per service | CONTRACT.md, the mandate line, `platform.yaml`, `teams.yaml`, `libs/platform_config.py`, all files of its service | Full new text of changed files, `hub_changes` (key, value, reason), notes. Same schema as 03. |
| Sub-lead | Opus 5.5 | 3 per tiered arm | The same rules, `platform.yaml`, the `service.yaml` of each service in its group, each of its workers' hub requests and notes | `requests`: key, value, one-line reason. May forward, merge, change, drop or add. No note field. |
| Platform lead | Opus 5.5 | 1 per arm | `platform.yaml`, the rules, the groups (names and services), the requests that reach it. Needs of services: only in `flat` (it manages all 8 directly) and `by-runtime-registry` (harness-attached registry). | Final value of every requested key and any override grants (03's hub-owner schema). Not called when no request reaches it. |

The worker step applies 03's mandate exactly: a Python file whose `charge()`/`send_email()` timeout is not `http_timeout("<service>")` is reverted (its other files kept), any `overrides:` a worker writes is stripped, only the platform lead grants overrides. The platform lead's decisions and grants are applied by the harness to a fresh scratch copy per arm, after the worker files.

### Arms

| Arm | Middle tier | Groups (sizes 3/2/3) | Why it is here |
|---|---|---|---|
| `flat` | none: the platform lead manages the 8 workers | n/a | Control. What the middle tier costs and what it loses in information. |
| `by-team` | 3 sub-leads, no owner team split | `rentals-and-money`: bookings-api, pricing-engine, payments-gateway. `fleet`: fleet-telemetry, maintenance-scheduler. `customer-and-back-office`: notifications, customer-profiles, admin-console. | Agent layers match team layers. Teams are grouped along the booking call graph. |
| `by-runtime` | 3 sub-leads by language | `python`: bookings-api, notifications, maintenance-scheduler. `jvm-and-go`: customer-profiles, payments-gateway. `node-ruby-rust`: pricing-engine, admin-console, fleet-telemetry. | The component-team cut. Splits Team Rentals and Team Fleet, and both of bookings-api's call edges. |
| `by-runtime-registry` | the same 3 sub-lead replies as `by-runtime` (reused, not re-called) | as `by-runtime` | A published Team API: the platform's needs registry reaches the platform lead. Does it substitute for an aligned cut? |

### Paired design

Per run index: the 8 workers run once; the `by-team` sub-leads run once; the `by-runtime` sub-leads run once and serve both `by-runtime` and `by-runtime-registry`; then four platform-lead calls. Worker files are identical across the four arm rows of an index, so differences in risks fixed and regressions come only from hub decisions and override grants. Arm order (the order of management calls) rotates per index.

### Metrics (per arm row)

| Metric | Definition |
|---|---|
| Risks fixed of 11, regressions, outside contract | `fixcheck.check()` on the arm's repo |
| Regressions by need | `pricing-engine.demand_model_timeout`, `payments-gateway.inbound_rate_per_caller` |
| Escalations | Requests that reach the platform lead |
| Harmful requests | A request (key, value) is harmful to a regression need when that need reads the key and the value, applied with no override, breaks the need's bound (parsed with `fixcheck.parse_duration` / `parse_rate`). Counted for worker requests and for requests reaching the platform lead, each split **inside** (the harmed service is in the requester's subtree: the sub-lead's group, or in `flat` the worker's own service) or **outside**. |
| Coupling ladder, per regression need | `not-threatened` (no worker request harms it), `absorbed-at-sub-lead` (a worker request harms it, no request reaching the platform lead does), `caught-at-top` (a request reaching the platform lead harms it, the need is not regressed), `reached-hub` (regressed after a harmful request reached the top), `introduced-by-top` (regressed with no harmful request reaching the top) |
| Overrides granted, distinct effective timeouts across the 8 services | As in 03 |
| Tokens by tier | Workers (shared, same in all four rows of an index), sub-leads, platform lead; management tokens = sub-leads + platform lead |
| Platform lead prompt size | Characters of the platform lead's prompt: span-of-control load, deterministic |
| Wall seconds | Workers, management |

### Traps and the choices that create the effect (disclosed in the README)

1. The mandate makes the hub the only seam for timeouts (03 showed local routes dissolve contention).
2. Requests-only upward protocol: a sub-lead whose services need nothing changed sends nothing, so a standing constraint (pricing's 300 ms) travels up only if a sub-lead that holds pricing also holds a service asking to change the timeout. The registry arm tests the alternative.
3. The runtime cut puts both services that want a longer timeout (bookings-api, notifications) under one sub-lead and away from pricing-engine and payments-gateway. Any cut that separates bookings-api from the services it calls would do the same; the result is about cutting the call graph, not about languages.
4. `by-team` does not contain the notifications-to-pricing coupling: notifications (Team Growth) and pricing (Team Rentals) share only the hub. A hub default couples every service, so no team cut fully contains it.
5. The flat lead sees all needs because it manages all services directly; the tiered platform lead does not. That is the information rule, same in every arm.
6. Teams are grouped into three domains by the authors (along the call graph); fleet-telemetry, unowned, sits with Team Fleet.

### Runs and cost

| Item | Count |
|---|---|
| Run indexes | 10 (40 arm rows) |
| Calls per index | 8 Sonnet workers + 6 Opus sub-leads + 4 Opus platform leads = 18 (fewer when no request reaches a platform lead) |
| Calls total | about 180, plus about 5% schema retries |
| Wall per index | workers about 40 s in parallel; management about 3 min sequential (about 1 min with `--parallel-arms`) |
| Wall total | about 40 min sequential, about 20 min parallel |
| Dry runs before freezing | 2 indexes (36 calls, about 8 min), kept outside `results/` |

---

## 05-event-bus

Org question: who looks after the shared channel?

### Components

| Component | What it is |
|---|---|
| Scanner (producer) | Deterministic. Publishes one `risk.reported` per catalogue risk (11): `{message_id, type, service, category, file, locator}` from `topologies.harbour.RISKS`. Order shuffled per run with `random.Random(run_index)`; ids `m01`..`m11` assigned after the shuffle. A stand-in for a detector: delivery is the question, not detection. |
| Subscriptions | Each team subscribes to the services it owns (pristine `owner.team`). fleet-telemetry has no subscriber. Same table in every arm. |
| Team consumers | Sonnet 5.5, one per team (6). One call per delivered message, sequential per team, teams in parallel. Gets CONTRACT.md, `platform.yaml`, `teams.yaml`, `libs/platform_config.py` (read-only), the message, all files of `message.service`. Told to fix only the reported risk. Returns `outcome` (fixed, not_mine, cannot_fix), `files` (paths limited to `message.service`), `reason`. May write overrides and code literals; `platform.yaml` is not in any path list. Consumers never know the arm. |
| Owner agent | Opus 5.5. `steward-agent`: the steward. `platform-owned`: the platform agent. None in `no-owner`. One call after pass 1, actions executed in pass 2, one round. |
| Investigator | Opus 5.5, one call at the end of every run, identical prompt in every arm. |

### Arms

| Mechanic | `no-owner` | `steward-agent` | `platform-owned` |
|---|---|---|---|
| Schema on publish (Datatype Channel) | no | no | yes: required fields, `service` and `category` enums, `file` an existing world file; failures go to the Invalid Message Channel. Cross-field consistency (file under service) is not checked. |
| Delivery | at-most-once: removed on delivery | at-most-once | at-least-once: ack on a valid consumer reply; a failed delivery is redelivered once, then Dead Letter Channel |
| Duplicate from transport | delivered | delivered | Idempotent Receiver: an acked message id is not delivered again |
| No subscriber | discarded | discarded | Dead Letter Channel |
| Consumer replies not_mine / cannot_fix | goes nowhere | recorded in the store | Dead Letter Channel |
| Records kept | each team's local log (receipt and outcome) | Message Store of every event (the steward taps the channel) | Message Store with Correlation Identifier and Message History, Dead Letter Channel, Invalid Message Channel |
| Owner view | none | the whole store | only the Dead Letter and Invalid Message channels |
| Corrective copies | n/a | fresh message ids, no link to the original | same message id as correlation, hop appended to history |

Action set, steward and platform agent alike: `redeliver(message_id)`, `route(message_id, service)` (rewrites `service`, delivers to that service's owner, or to an assigned team), `assign_owner(service, team)` (adds a subscription and delivers that service's undelivered messages), `return_to_producer(message_id)` (the scanner republishes a valid message for the same risk). Unknown ids are recorded and ignored.

### Faults

Injected by the harness, identical per run index in every arm. Routable risks R, catalogue order without fleet-telemetry: `bookings-api.missing_timeout`, `bookings-api.no_rate_limit`, `payments-gateway.hardcoded_secret`, `customer-profiles.missing_backup`, `pricing-engine.unpinned_dependency`, `admin-console.public_exposure`, `notifications.missing_timeout`, `notifications.unpinned_dependency`, `maintenance-scheduler.missing_backup`.

| Kind | Target in run *i* (0-based) | What happens |
|---|---|---|
| `malformed` | R[i mod 9] | `category`, `file` and `locator` removed; `service` kept, so it routes |
| `wrong-service` | R[(i+2) mod 9] | `service` set to the next service in `SERVICES` order owned by a different team, skipping fleet-telemetry; `file`, `locator`, `category` kept (02's fault) |
| `consumer-crash` | R[(i+4) mod 9] | The first delivery returns an injected failure; no model call |
| `duplicate` | R[(i+6) mod 9] | The transport delivers it a second time after the first is processed |
| unroutable (natural) | both fleet-telemetry risks, every run | No team owns fleet-telemetry |

Schedule for n=10 (run 10 repeats run 1):

| Run | malformed | wrong-service (moved to) | consumer-crash | duplicate |
|---|---|---|---|---|
| 1 | bookings.missing_timeout | payments.hardcoded_secret (pricing-engine) | pricing.unpinned_dependency | notifications.missing_timeout |
| 2 | bookings.no_rate_limit | customer-profiles.missing_backup (maintenance-scheduler) | admin.public_exposure | notifications.unpinned_dependency |
| 3 | payments.hardcoded_secret | pricing.unpinned_dependency (admin-console) | notifications.missing_timeout | maintenance.missing_backup |
| 4 | customer-profiles.missing_backup | admin.public_exposure (bookings-api) | notifications.unpinned_dependency | bookings.missing_timeout |
| 5 | pricing.unpinned_dependency | notifications.missing_timeout (payments-gateway) | maintenance.missing_backup | bookings.no_rate_limit |
| 6 | admin.public_exposure | notifications.unpinned_dependency (payments-gateway) | bookings.missing_timeout | payments.hardcoded_secret |
| 7 | notifications.missing_timeout | maintenance.missing_backup (notifications) | bookings.no_rate_limit | customer-profiles.missing_backup |
| 8 | notifications.unpinned_dependency | bookings.missing_timeout (customer-profiles) | payments.hardcoded_secret | pricing.unpinned_dependency |
| 9 | maintenance.missing_backup | bookings.no_rate_limit (customer-profiles) | customer-profiles.missing_backup | admin.public_exposure |
| 10 | bookings.missing_timeout | payments.hardcoded_secret (pricing-engine) | pricing.unpinned_dependency | notifications.missing_timeout |

Per run: 2 unroutable, 3 loss faults, 1 duplicate, 5 clean messages. Four targets (the two bookings-api and two notifications risks) share a service with a sibling risk, so a consumer can fix them from the sibling's message: that is counted as `absorbed`, not hidden.

### Ground truth and the investigator

The harness labels each risk: fixed at the end (checker), processed (some delivery of a message for it got outcome `fixed`), and for an unfixed risk a cause: `no_subscriber` (unroutable, never assigned), `missing_fields` (malformed target, never processed fixed), `wrong_team` (wrong-service target, never processed fixed), `consumer_failed` (crash target, never processed fixed), `consumer_declined` (a clean message declined), `fix_did_not_hold` (consumer said fixed, checker disagrees). The first four are **channel-caused**.

The investigator gets the scanner's published list (id, risk id, service), the subscription table and the arm's records, and returns for each of the 11 risks `status` (handled, lost) and `cause` (none, no_subscriber, missing_fields, wrong_team, consumer_failed, consumer_declined, unknown) with evidence message ids. Scored per run: **attribution accuracy** (correct cause / channel-caused losses) and **false assurance** (status `handled` on a channel-caused loss; reported separately for `fix_did_not_hold`, which no channel can see). In `platform-owned` a second, paired call gets the same records with Correlation Identifiers and Message History removed and copies re-labelled with fresh ids.

### Metrics (per arm run)

| Metric | Definition |
|---|---|
| Risks fixed of 11, regressions, outside contract | `fixcheck.check()` |
| Clean-message fix rate | Fixed / 5 clean targets. The control: should be about equal across arms. |
| Fault outcome, per loss fault | `recovered` (target fixed and its own message processed fixed), `absorbed` (fixed without its own message processed fixed), `lost` |
| Unowned service | fleet-telemetry risks fixed, of 2 |
| Duplicate | consumer calls on the duplicate, second call rewrote files, harm (rewrote and target unfixed at the end) |
| Wrong-service | the wrong team declined (not_mine or cannot_fix) vs changed its own files (bogus edit) |
| Traceability | investigator attribution accuracy, false assurance; platform-owned paired accuracy without trace context |
| Tokens | consumers, owner agent, investigator (the ablation call reported apart, not in the arm total); consumer calls; owner prompt characters |
| Wall seconds | per run |

### Traps and the choices that create the effect (disclosed in the README)

1. The platform-owned guarantees are code. Consumer-crash recovery and duplicate suppression there are by construction; the run measures what their absence costs, whether an agent steward replaces them, and the price.
2. The unroutable messages are the fixture's own unowned service, not an injected fault, and they exist in every arm.
3. The schema does not check that `file` sits under `service`, so wrong-service passes it in every arm. A stricter schema would turn it into a malformed message.
4. One corrective round, same action set for steward and platform agent; the difference is their view (whole store vs exceptions) and the code underneath.
5. Four faults per run is a stress density chosen to get 10 faults per kind per arm in one evening; faults sit on distinct messages so they do not compound by design.
6. Consumers are told to fix only the reported risk; opportunistic fixes are counted as absorbed.
7. No-owner teams keep local logs, so the investigator is not blind there; it lacks a shared store.

### Runs and cost

| Item | Count |
|---|---|
| Runs | 10 per arm, 30 |
| Consumer calls per run | about 9 (no-owner), 13 (steward-agent), 12 (platform-owned) |
| Opus calls per run | 1 (no-owner), 2 (steward-agent), 3 (platform-owned, including the ablation) |
| Calls total | about 340 Sonnet + 60 Opus = about 400, plus retries |
| Wall per run | about 2 to 3 min (Team Rentals processes 3 to 4 messages in sequence, then pass 2 and the Opus calls) |
| Wall total | about 75 to 90 min sequential, about 30 min with `--parallel-arms` |
| Dry runs before freezing | 2 per arm (6 runs, about 15 min), kept outside `results/` |

---

## Pre-registration

`predictions-draft.md` holds the draft predictions, marked DRAFT. Each `experiment.py` refuses to run while that file is missing or contains "DRAFT", or while root `PREDICTIONS.md` lacks the folder's heading (`## 04-hierarchical:` or `## 05-event-bus:`). To freeze: run the dry runs, fill "What we had seen before writing this", get approval, remove the DRAFT marker, append the section to `PREDICTIONS.md`, commit. The appended text must not contain the word DRAFT anywhere: 02's and 03's `experiment.py` refuse while `PREDICTIONS.md` does. After the first benchmark run, any change to the harness that can move a metric needs a dated README note and a full re-run.

## Combined budget for Monday

About 580 model calls, about 2 h sequential (about 1 h with parallel arms), plus about 25 min of dry runs.
