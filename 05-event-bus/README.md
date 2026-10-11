# 05 · Event bus: who looks after the shared channel

## The question

Agents that talk only through a shared message channel need someone to look after it. When a message is malformed, lands with the wrong team, reaches a consumer that crashes, arrives twice, or has no subscriber at all, who notices, and who fixes it? And when a risk is never fixed, can anyone trace the failure back to where it started?

## The short answer

TBD after the benchmark.

## The experiment

The shared world is Harbour Bikes as real files: [`fixtures/harbour-bikes/`](../fixtures/harbour-bikes/) (eight services, 11 planted risks, one `service.yaml` per service with its owning team). The same world and the same checker serve folders 02 to 04. Here the services are fixed through a message channel instead of a plan or a merge:

- **The scanner** is deterministic: it publishes one `risk.reported` message per planted risk (11), `{message_id, type, service, category, file, locator}`, in an order shuffled per run and numbered `m01` to `m11` after the shuffle. It stands in for a detector: delivery is the question, not detection.
- **Subscriptions.** Each team subscribes to the services it owns, read from the pristine `service.yaml` files: Team Rentals (bookings-api, pricing-engine), Team Money (payments-gateway), Team Growth (notifications), Team Identity (customer-profiles), Team Fleet (maintenance-scheduler), Team Ops Tools (admin-console). fleet-telemetry has `owner: null` in the fixture, so nobody subscribes to it and its two messages are unroutable in every run, in every arm.
- **The consumers** (Claude Sonnet 5.5, one per team, six) get one call per delivered message, sequentially within a team, teams in parallel. They get [`CONTRACT.md`](../fixtures/harbour-bikes/CONTRACT.md), the read-only context (`platform.yaml`, `teams.yaml`, `libs/platform_config.py`), the message and every file of the service the message names. They are told to fix only the reported risk, and to reply `not_mine` or `cannot_fix` and change nothing otherwise. They answer in one reply with the full new text of every file they change, no tools. They never know the arm. The harness writes the files.
- **The owner agent** (Claude Opus 5.5) gets one call after the first pass and one round of actions, executed in a second pass. There is none in `no-owner`.
- **The investigator** (Claude Opus 5.5) gets one call at the end of every run with the same prompt in every arm.

Three arms, one question each: who owns the channel, and what can they see?

| Mechanic | `no-owner` | `steward-agent` | `platform-owned` |
|---|---|---|---|
| Schema on publish (Datatype Channel) | no | no | yes: required fields, `service` and `category` enums, `file` an existing world file. Failures go to the Invalid Message Channel. Cross-field consistency (file under service) is not checked. |
| Delivery | at-most-once: removed on delivery | at-most-once | at-least-once: ack on a valid consumer reply; a failed delivery is redelivered once, then goes to the Dead Letter Channel |
| Duplicate from the transport | delivered | delivered | Idempotent Receiver: a team that acknowledged a message id is not delivered it again |
| No subscriber | discarded | discarded | Dead Letter Channel |
| Consumer replies `not_mine` or `cannot_fix` | goes nowhere | recorded in the store | Dead Letter Channel |
| Records kept | each team's local log (receipt and outcome) | Message Store of every event (the steward taps the channel) | Message Store with Correlation Identifier and Message History, Dead Letter Channel, Invalid Message Channel |
| Owner view | none | the whole store | only the Dead Letter and Invalid Message channels |
| Corrective copies | n/a | fresh message ids, no link to the original | same message id as Correlation Identifier, hop appended to Message History |

The action set is the same for the steward and the platform agent: `redeliver(message_id)`, `route(message_id, service)` (rewrites `service`, delivers to that service's team), `assign_owner(service, team)` (adds a subscription and delivers that service's undelivered messages), `return_to_producer(message_id)` (the scanner republishes a valid message for the same risk). Unknown ids are logged and ignored. The two owners get the same instructions; only the sentence that says what they are shown differs.

### Faults

Injected by the harness, identical per run index in every arm, on four distinct routable risks (the nine risks of services that have an owner, in catalogue order, `R`). Run *i* (0-based) uses:

| Fault | Target | What happens |
|---|---|---|
| `malformed` | `R[i mod 9]` | `category`, `file` and `locator` are removed; `service` is kept, so it still routes |
| `wrong-service` | `R[(i+2) mod 9]` | `service` is set to the next service in alphabetical order owned by a different team, skipping fleet-telemetry; the file, locator and category are kept |
| `consumer-crash` | `R[(i+4) mod 9]` | the first delivery returns an injected failure with no model call |
| `duplicate` | `R[(i+6) mod 9]` | the transport delivers it a second time after the first is processed |

Per run that is 2 unroutable messages (fleet-telemetry), 3 loss faults, 1 duplicate and 5 clean messages. Over runs 0 to 8 each fault kind lands on all nine routable risks once. There is no separate `dropped` fault: a dropped message looks to the channel exactly like a crash before the reply or a message with no subscriber, so those two stand for it. Four targets (the two bookings-api and two notifications risks) share a service with a sibling risk, so a consumer can fix one from the sibling's message. That is counted as `absorbed`, not hidden.

### Ground truth and the investigator

The harness labels every risk: fixed at the end (checker), processed (some delivery of a message for it got the outcome `fixed`), and for an unfixed risk a cause: `no_subscriber`, `missing_fields`, `wrong_team`, `consumer_failed` (these four are channel-caused), `consumer_declined`, `fix_did_not_hold` (a consumer said fixed, the checker disagrees) or `unknown`.

The investigator gets the scanner's ledger (message id, risk, the service the scanner meant), the subscription table and the arm's records. For each of the 11 risks it says `status` (`handled` or `lost`), `cause` (where a lost risk was lost) and `first_problem` (the first thing that went wrong with the message, even if it was put right later), citing message ids. It is a measured subject, scored against the harness's ground truth, never a judge:

- **first-problem accuracy (headline)**: on the six risks that had a problem at the start (the malformed, wrong-service, consumer-crash and duplicate targets, and the two unowned fleet-telemetry risks), did it name `missing_fields`, `wrong_team`, `consumer_failed`, `delivered_twice` or `no_subscriber`? Every arm gets six scored items per run whether or not the owner repaired the fault, so the measure does not vanish when an arm fixes everything. False alarms (a problem named on the five clean risks) are reported too.
- **cause accuracy (secondary)**: correct cause over the channel-caused losses still unfixed at the end; a risk left out counts as wrong. Few arms have many of these.
- **false assurance (secondary)**: a channel-caused loss reported as handled. Reported separately for `fix_did_not_hold`, which no channel can see.

A run whose investigator call failed, or whose answer repeats a risk, is counted in a failures table and excluded from these rates, never scored as wrong. Owner call failures and consumer error or invalid replies are counted there too.

In `platform-owned` a second, paired call gets the same records with Correlation Identifiers and Message History removed and corrective copies relabelled with fresh ids. It is reported apart and is not part of the arm's token total.

### Scoring

Fixed and regressed come from the shared checker only ([`fixcheck.check()`](../src/topologies/fixcheck.py), rules in [`CHECKER.md`](../fixtures/harbour-bikes/CHECKER.md)). Every other metric is computed by code from the run record: no model judges and nothing parses prose. The answer key `CHECKER.md` is never copied into the scratch repo. The predictions were written before any benchmark run: [`PREDICTIONS.md`](../PREDICTIONS.md).

## Results

TBD after the benchmark. Source: `results/runs.jsonl` and `results/summary.md`.

## Predictions

TBD: the frozen predictions go in [`PREDICTIONS.md`](../PREDICTIONS.md) before any benchmark run, and the hit or miss table goes here after it.

## Limits

- One task, small n: ten runs per arm, 30 in total.
- Claude models only: Sonnet 5.5 consumers, Opus 5.5 steward, platform agent and investigator, one backend (`claude-cli`).
- The harness applies the edits and the bus is simulated in-process. This measures who looks after a channel and what the platform's guarantees are worth, not how a real broker behaves.
- The platform guarantees are code. Crash recovery and duplicate suppression in `platform-owned` are by construction; the run measures what their absence costs, whether an agent steward replaces them, and the price.
- The unroutable messages are the fixture's own unowned service, not an injected fault, and they exist in every arm.
- The schema does not check that `file` sits under `service`, so wrong-service passes it in every arm. A stricter schema would turn it into a malformed message.
- One corrective round, the same action set for steward and platform agent. The difference is their view (whole store against exceptions) and the code underneath.
- Four faults per run is a stress density chosen to get ten faults per kind per arm in one evening. The faults sit on distinct messages so they do not compound by design.
- Consumers are told to fix only the reported risk. Opportunistic fixes are counted as `absorbed`.
- No-owner teams keep local logs, so the investigator is not blind there; it lacks a shared store.
- Choices made while building the harness: the Idempotent Receiver keys on team and message id and suppresses transport duplicates only, so an owner's redelivery, route or republished copy is always delivered; a transport duplicate is not queued when the first processing failed, because the platform redelivers that itself; a message counts as processed only when it was complete (all fields, the risk's own service, the owning team), so a consumer's guessed "fixed" on a malformed or misrouted message does not; files in a consumer reply are written whatever its `outcome` says, so a bogus edit by a wrong team is measured; the ledger shows the service the scanner meant, not the faulted one; the owners' action logs name the message they acted on but never the id of the copy; after `assign_owner` the receiving team's prompt lists the new service among those it owns.
- Dry runs before the benchmark are disclosed in `PREDICTIONS.md`.
- Token counts include Claude Code's fixed prompt per call (see the [root README](../README.md#setup)); they are counts, not cost. The headline token column is operating tokens (consumers plus owner); the investigator is reported apart because its prompt grows with the records the arm keeps.
- Comparisons hold inside this folder only, never against 01 to 04.

## Run it yourself

```bash
uv sync
uv run pytest                                                                       # no model calls
uv run 05-event-bus/run.py --arm platform-owned --out ~/.cache/issue15/05-scratch.jsonl
uv run 05-event-bus/run.py --arm no-owner --run-index 3 --out ~/.cache/issue15/05-scratch.jsonl
uv run 05-event-bus/experiment.py                                                   # 3 arms, n=10, results/runs.jsonl
uv run 05-event-bus/experiment.py --parallel-arms
uv run 05-event-bus/experiment.py --summarise-only                                  # rewrite results/summary.md
```

`experiment.py` refuses to run while the draft predictions say DRAFT or `PREDICTIONS.md` has no `## 05-event-bus:` section; `run.py` is always allowed and refuses to write `results/runs.jsonl`. Keep one setup per `runs.jsonl`: the summary refuses a file that mixes models, served models or backends. Every row records the git commit it ran on, with `-dirty` appended when this folder (results aside), `src` or `fixtures` had uncommitted changes; the summary refuses dirty, unknown or mixed commits, so commit before the benchmark.

## What's in the code

| File | What it does |
|---|---|
| [`bus.py`](bus.py) | The run: publish, two delivery passes under the arm's rules, the owner round, scoring through the shared checker, the investigator calls and the run record. |
| [`bus_channel.py`](bus_channel.py) | The in-process channel: queues, schema, Message Store, team logs, Dead Letter and Invalid Message channels, acknowledgements, corrective copies. |
| [`bus_config.py`](bus_config.py) | The models, the arms and their policies, the fault offsets, ownership and subscriptions read from the pristine fixture. |
| [`bus_faults.py`](bus_faults.py) | The scanner and the deterministic fault schedule. |
| [`bus_owner.py`](bus_owner.py) | What each owner is shown, its one call, and the code that carries out its actions. |
| [`bus_prompts.py`](bus_prompts.py) | Every prompt and reply schema. |
| [`bus_trace.py`](bus_trace.py) | Ground truth per risk, the investigator's records (with and without trace context) and its scoring. |
| [`run.py`](run.py) | One run of one arm, printed. |
| [`experiment.py`](experiment.py) | The benchmark loop, the pre-registration guard and the summary. |
