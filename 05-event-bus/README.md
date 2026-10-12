# 05 · Event bus: who looks after the shared channel

## The question

Agents that talk only through a shared message channel need someone to look after it. When a message is malformed, lands with the wrong team, reaches a consumer that crashes, arrives twice, or has no subscriber at all, who notices, and who fixes it? And when a risk is never fixed, can anyone trace the failure back to where it started?

## The short answer

Someone has to look after the channel. With no owner, 4 or 5 of the 11 risks were lost in every run (6.7 of 11 fixed, minimum 6). The two unroutable fleet-telemetry risks were never fixed (0 of 20), every wrong-service message and every consumer crash was lost (10 of 10 each), and a malformed message was only fixed by accident: absorbed in 7 of 10 runs, lost in 3.

An agent steward and a platform-owned channel fixed the same. Both fixed all 11 risks in all 10 runs (11.0 of 11), recovered every wrong-service and consumer-crash fault, and fixed fleet-telemetry 20 of 20. The platform did it cheaper and quieter: 55,700 operating tokens against 71,106 (about 22% fewer), 2,480 owner tokens against 8,249, an owner prompt of 1,683 characters against 7,336. The steward raised 3 false alarms in 50 clean risks, the platform 0.

Two caveats on that comparison. The duplicate was processed twice in 0 of 10 runs under `platform-owned` and 10 of 10 under the other two, but that is by construction: the Idempotent Receiver is code. And the traceability question has no answer here: the investigator named the first problem in 60 of 60 cases in every arm, including `platform-owned` with the trace context stripped. That is the ceiling, and it is the result we predicted, so trace context showed no effect on this task.

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

Run on 11 Oct 2026, n=10 per arm, on freeze commit `0db1298` (all 30 rows recorded that commit, clean). Tokens count everything sent and received. Operating tokens are consumers plus owner; the investigator is reported apart.

| Arm | Risks fixed of 11 (mean, min) | Clean risks fixed | fleet-telemetry fixed (of 20) | Operating tokens (consumers / owner) | Investigator tokens | Consumer calls | Owner prompt chars | Wall seconds |
|---|---|---|---|---|---|---|---|---|
| `no-owner` | 6.7 (6) | 50 of 50 | 0 | 41,790 (41,790 / 0) | 4,336 | 9.0 | 0 | 34 |
| `steward-agent` | 11.0 (11) | 50 of 50 | 20 | 71,106 (62,858 / 8,249) | 6,961 | 14.3 | 7,336 | 63 |
| `platform-owned` | 11.0 (11) | 50 of 50 | 20 | 55,700 (53,220 / 2,480) | 7,029 | 12.0 | 1,683 | 57 |

No arm had a regression. Loss faults, per arm and kind (recovered: the target was fixed through its own message; absorbed: fixed through another message; lost):

| Arm | `malformed` | `wrong-service` | `consumer-crash` |
|---|---|---|---|
| `no-owner` | 0 recovered, 7 absorbed, 3 lost | 10 lost | 10 lost |
| `steward-agent` | 5 recovered, 5 absorbed | 10 recovered | 10 recovered |
| `platform-owned` | 10 recovered | 10 recovered | 10 recovered |

Duplicate: processed twice in 10 of 10 runs for `no-owner` and `steward-agent`, 0 of 10 for `platform-owned`. The second processing rewrote files in 0 runs, so there was no harm anywhere. The wrong team declined the misrouted message in 10 of 10 runs in every arm and never edited its own files.

Traceability, headline (first problem named, on the 6 risks per run that had one):

| Arm | Records | First problem correct | False alarms on clean risks |
|---|---|---|---|
| `no-owner` | as kept | 60 of 60 | 0 of 50 |
| `steward-agent` | as kept | 60 of 60 | 3 of 50 |
| `platform-owned` | with trace context | 60 of 60 | 0 of 50 |
| `platform-owned` | trace context stripped | 60 of 60 | 0 of 50 |

Secondary: in `no-owner`, 43 channel-caused losses were left at the end, the investigator gave the right cause for 43 of 43 and reported none of them as handled. The other arms left no channel-caused loss, so those cells have nothing to score. Failures: 0 owner calls and 0 investigator calls failed in any arm; 1 consumer delivery in `no-owner` had an error or invalid reply, none elsewhere.

What this tells us:

- **No owner loses about 4 of 11 every run.** 6.7 of 11, lost per run 4 or 5. The clean risks were fixed 50 of 50 in every arm, so consumers are fine when the channel delivers.
- **A consumer cannot fix what it is never given.** fleet-telemetry has no owner, so no team subscribes: 0 of 20 without an owner, 20 of 20 in both owned arms.
- **Wrong-service and consumer-crash faults are lost without an owner, 10 of 10 each.** The wrong team declined every misrouted message and nobody sent it on.
- **Malformed messages are fixed by luck.** 7 absorbed and 3 lost. A malformed message keeps its `service`, so the consumer sees the service's files and may fix the risk anyway, but not reliably.
- **A steward agent matched the platform on recovery.** Both ended with 11.0 of 11 and recovered every loss fault, so reading the whole log and acting on it was a full substitute for the guarantees in this task.
- **The platform was cheaper and quieter.** 55,700 operating tokens against 71,106 (about 22% fewer), 2,480 owner tokens against 8,249, 12.0 consumer calls against 14.3, an owner prompt of 1,683 characters against 7,336 (about 4.4x smaller). The steward raised 3 false alarms on clean risks in 50, the platform 0. The steward recovered 5 of 10 malformed faults and absorbed the other 5; the platform recovered all 10 by rejecting and returning them.
- **The duplicate result is by construction.** The platform processed the duplicate twice 0 of 10 times because the Idempotent Receiver is code. The duplicate caused no harm in any arm, so the data does not show that the missing guarantee cost anything here.
- **The investigator named the first problem every time, in every arm.** 60 of 60, including with the trace context stripped. One task with this few faults was too easy for this to separate anything. We cannot say whether Correlation Identifiers help on a harder trail.

Raw data: [`results/summary.md`](results/summary.md) for the full tables, [`results/runs.jsonl`](results/runs.jsonl) for every run (records, deliveries, owner actions, ground truth and investigator answers).

## Predictions

Written before any run and frozen on 11 Oct 2026 in [`PREDICTIONS.md`](../PREDICTIONS.md) (commit `0db1298`). Results that disagree are reported as misses. The dry runs are disclosed there.

| Prediction | Result |
|---|---|
| Risks fixed: `no-owner` lowest, about 6 to 8; `platform-owned` highest, about 10 to 11 | **Hit.** 6.7 (min 6) and 11.0. |
| Risks fixed: `steward-agent` in the middle, about 9 to 10 | **Miss.** 11.0, equal to `platform-owned`. |
| Clean fix rate about equal across arms, near 5 of 5 | **Hit.** 50 of 50 in every arm. |
| fleet-telemetry: 0 of 20 without an owner, most in the owned arms | **Hit.** 0, 20 and 20 of 20. |
| `malformed`, `no-owner`: never recovered, absorbed or lost, absorbed only where the target shares a service with a sibling (5 of 10 schedule slots) | **Mixed.** Never recovered, 7 absorbed and 3 lost. The absorbed runs include single-risk services (customer-profiles, admin-console, maintenance-scheduler), so the mechanism was wrong: a malformed message keeps its `service`, which is enough for a consumer to read the files. |
| `malformed`, `steward-agent`: mostly absorbed or lost, recovered when the steward returns it | **Mixed.** 5 recovered, 5 absorbed, 0 lost. Not "mostly lost". |
| `malformed`, `platform-owned`: recovered in most | **Hit.** 10 of 10. |
| `wrong-service`: lost in most without an owner, recovered in most by the steward and the platform agent | **Hit.** 10 lost; 10 recovered; 10 recovered. |
| `consumer-crash`: lost in most without an owner; recovered in nearly all by the platform | **Hit.** 10 lost; 10 recovered. |
| `consumer-crash`, `steward-agent`: recovered in most, some lost | **Mixed.** 10 recovered, none lost. |
| Duplicate processed twice: 10 of 10, 10 of 10, 0 of 10 (by construction) | **Hit.** Exactly that. |
| Duplicate rewrites rare, harm near 0 | **Hit.** 0 rewrites, 0 harm. |
| Wrong team declined in most runs in every arm | **Hit.** 10 of 10 in every arm, no bogus edits. |
| Operating tokens: `no-owner` lowest, `platform-owned` middle at about 1.4 times `no-owner` | **Hit.** 41,790 and 55,700 (1.33x). |
| Operating tokens: `steward-agent` highest, about 2 to 2.5 times `no-owner` (80k) | **Mixed.** Highest, but 71,106 is 1.7x, below the range. |
| Consumer calls: about 9, 13 to 15, about 12 | **Hit.** 9.0, 14.3, 12.0. |
| Owner prompt: steward several times `platform-owned` | **Hit.** 7,336 against 1,683 (about 4.4x). |
| First problem correct at or near the ceiling in every arm, and the same with the trace context stripped | **Hit.** 60 of 60 in every row, including stripped. |
| False alarms on clean risks low in every arm | **Hit.** 0, 3 and 0 of 50. |
| Cause mostly correct, false assurance near zero (thin counts) | **Hit.** 43 of 43 and 0 of 43 in `no-owner`; no channel-caused losses left in the owned arms. |
| Failures: owner and investigator failures near zero, a few invalid consumer replies in every arm | **Mixed.** Owner and investigator failures were 0. Invalid or errored consumer deliveries: 1 in `no-owner`, 0 in the others, not "in every arm". |

Score: 15 hits, 1 miss, 5 mixed, over the 21 rows above.

The surprises we named did not happen. There was no gap in first-problem traceability (60 of 60 everywhere). `steward-agent` matched `platform-owned` on lost faults (none) but not at no more operating tokens: it used about 28% more (71,106 against 55,700). `no-owner` consumers did not absorb most loss faults: 7 of 30 were absorbed, all of them malformed messages.

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
- Traceability hit the ceiling (60 of 60 in every arm, stripped trace included). With this few faults and this easy a trail, the investigator cannot separate the arms, so the Correlation Identifier result is "no effect seen on this task", not "no effect".
- `no-owner` shows 1.0 outside contract on average: that is fleet-telemetry's planted `no_owner` risk, which nobody fixes, labelled by the frozen checker as outside contract (the same label artifact as in folder 03). Risks-fixed counts are unaffected. *Fixed 2026-10-12* in the checker (see `CHECKER.md`); relabelled from the stored per-risk results the column is 0.0, and the table keeps the label as recorded.
- The steward and the platform agent end at the same 11.0 of 11, so the arms separate on cost and false alarms only, at n=10. The 22% token gap and the 3 false alarms in 50 are single-task numbers.
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
