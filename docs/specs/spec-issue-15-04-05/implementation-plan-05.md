# Implementation plan: 05-event-bus

For a coding agent with no other context. Read first: `SPEC.md` and `experiment-design.md` in this folder (the 05 half), then `03-fan-out-partial-reducer/` (`fanout.py`, `fanout_prompts.py`, `experiment.py`, `run.py`, `tests/fanout_helpers.py`, `README.md`) and `02-pipeline-checkpoint/ckpt_faults.py` for conventions. 05 uses the same client, guards, checker, record and summary style.

## Ground rules

- Write only under `05-event-bus/`. Do not edit `src/`, `fixtures/`, `tests/` (root), `01`-`04`, `pyproject.toml`, root `README.md` or `PREDICTIONS.md`. The coordinator has already added `05-event-bus/tests` to `pyproject.toml` testpaths; if it is missing, run `uv run pytest 05-event-bus/tests` and say so in your report, do not edit the file.
- 05 imports nothing from 03 or 04. If you need a 3-line helper that exists there (`code_commit`, `score_record`, `rotated`), copy it.
- Every model call goes through `topologies.model.Backend` and `topologies.guards.call_and_validate`.
- Score fixes only with `topologies.fixcheck.check()`. Every other metric is computed by code from the run record.
- Tests make no model calls (`ScriptedBackend`).
- Baseline before you start: `uv run pytest` shows 305 passed. Report it.
- Do not commit.

## Glossary used in code and README

Publish-Subscribe Channel, Datatype Channel (the schema), Invalid Message Channel, Dead Letter Channel, Correlation Identifier, Message History, Idempotent Receiver, Message Store. Do not write "DLQ", "topic", "trace id" or "dedup" in prose or identifiers (`dead_letters`, `invalid`, `store`, `correlation_id`, `history` are fine).

## Files

### `bus_config.py`

```python
CONSUMER_MODEL = "claude-sonnet-5-5"
OWNER_MODEL = "claude-opus-5-5"          # steward, platform agent, investigator
ARMS = ("no-owner", "steward-agent", "platform-owned")
FAULT_KINDS = ("malformed", "wrong-service", "consumer-crash", "duplicate")
FAULT_OFFSETS = {"malformed": 0, "wrong-service": 2, "consumer-crash": 4, "duplicate": 6}
```
Plus, computed from the pristine fixture (never from a scratch repo): `OWNER = {service: team or None}` from `service.yaml` `owner.team`; `TEAMS` from `teams.yaml` (names and contacts); `SUBSCRIPTIONS = {team: [services]}` (teams with no service still listed, empty); `ROUTABLE = [risk.id for risk in RISKS if OWNER[risk.service]]` in catalogue order (9 ids; assert it).

`Policy` dataclass per arm: `schema_check`, `acks`, `idempotent_receiver`, `dead_letter`, `trace_context`, `keep_store`, `owner_view` (`"none" | "store" | "exceptions"`).

| arm | schema_check | acks | idempotent_receiver | dead_letter | trace_context | keep_store | owner_view |
|---|---|---|---|---|---|---|---|
| no-owner | F | F | F | F | F | F | none |
| steward-agent | F | F | F | F | F | T | store |
| platform-owned | T | T | T | T | T | T | exceptions |

### `bus_faults.py`

- `faults_for_run(index: int) -> dict[str, str]`: kind → risk id, `ROUTABLE[(index + FAULT_OFFSETS[kind]) % 9]`. Assert the four targets are distinct.
- `wrong_destination(service) -> str`: the next service in `SERVICES` order (wrapping) that is not fleet-telemetry and whose owner team differs from `OWNER[service]`.
- `scanner_messages(index) -> list[dict]`: one `{"type": "risk.reported", "service", "category", "file", "locator"}` per catalogue risk, shuffled with `random.Random(index)`, then ids `m01`..`m11` in that order. Also returns `risk_of = {message_id: risk_id}`.
- `malform(body) -> body`: drop `category`, `file`, `locator`.
- `misroute(body) -> body`: `service = wrong_destination(service)`, the rest kept.
- `valid_copy(risk_id) -> body`: the scanner's correct body for `return_to_producer`.

### `bus_channel.py`

`Message` dataclass: `message_id`, `body` (dict), `correlation_id` (str or ""), `history` (list of str hops). `Bus(policy, subscriptions)` with:

- `publish(message) -> str`: returns where it went: `queued:<team>`, `invalid` (schema fails, platform only), `dead-letter:no-subscriber` (platform), `discarded:no-subscriber` (others). Schema (`DATATYPE_SCHEMA`, JSON Schema plus one check): required `message_id, type, service, category, file, locator`; `service` enum `SERVICES`; `category` enum of catalogue categories; `file` in `world_files(FIXTURE)`. It does not check that `file` sits under `service` (say so in a comment: wrong-service passes by design).
- `route(service) -> team | None`: from `subscriptions` plus assignments made by `assign_owner`.
- Store events (only when `keep_store`): `{"seq", "event": published|delivered|outcome|no_reply|invalid|dead_letter|discarded|action, "message_id", "service", "team", "outcome", "reason", "correlation_id", "history"}`. `correlation_id` and `history` are filled only when `trace_context`.
- `team_logs`: always kept: per team, `received` and `outcome` entries (a crashed delivery leaves `received` only).
- `dead_letters` and `invalid`: lists of `{message, reason}` (platform only).
- `acked: set[str]` for the Idempotent Receiver.
- `copy_for_retry(message, hop) -> Message`: with `trace_context`, same id and `correlation_id = original id`, `history + [hop]`; without, a fresh id `c01`, `c02`, ... and no link.

### `bus_prompts.py`

- `REFERENCE_FILES = ("platform.yaml", "teams.yaml", "libs/platform_config.py")`.
- `CONSUMER_SYSTEM`, identical in every arm:

  > You are the agent of {team} at a bike-rental platform. Your team owns: {services}. Messages arrive on a shared channel; each `risk.reported` message reports one operational risk in one service.
  > Fix the risk this message reports, and only that risk, in the service the message names. If the message is not about a service your team owns or about a file of that service, reply `not_mine`. If you cannot find or cannot fix the reported risk, reply `cannot_fix`. In both cases change no files.
  > The rules for changing the services (fixes are scored automatically by reading the files; a change outside these rules does not count): {contract}
  > Return the full new text of every file you change and leave unchanged files out. Reply with JSON only, no prose, matching: {"outcome": "fixed|not_mine|cannot_fix", "files": [{"path": "<file path as shown>", "content": "<full new file text>"}], "reason": "<one line>"}

- `consumer_prompt(repo, message_body)`: the message as JSON (without `correlation_id`/`history`, which consumers never see), then the reference files, then every file of `message_body["service"]` read from the arm's scratch repo. If the message has no usable `service`, the files section says so.
- `consumer_schema(paths)`: `outcome` enum, `files` items with `path` enum = the files of `message.service` (empty enum allowed), `reason` ONE_LINE.
- `ACTIONS_SCHEMA`: `{"actions": [{"action": enum[redeliver, route, assign_owner, return_to_producer], "message_id": str, "service": enum SERVICES (route, assign_owner), "team": enum team names (assign_owner), "reason": ONE_LINE}]}`, max 12 items.
- `STEWARD_SYSTEM`:
  > You look after the shared message channel of a bike-rental platform. Every message must reach a team that fixes what it reports. Below is everything the channel recorded: each message published, delivered, and the outcome its consumer reported. Decide what to do about messages that did not end in a fix. Actions: redeliver a message to its team; route a message to another service (rewrites its service and delivers it to that service's team); assign an owner team to a service that has none (its undelivered messages then go to that team); return a message to the producer (it republishes a valid message for the same risk). You get one round of actions. Reply with JSON only, matching: {schema}
- `PLATFORM_AGENT_SYSTEM`: the same text with "Below is everything the channel recorded..." replaced by "The platform's channel validates every message, retries a failed delivery once and records every hop. Below are only the exceptions: the Dead Letter Channel (messages no team took, or that a team declined, or that failed twice) and the Invalid Message Channel (messages that broke the schema)." Every other sentence identical.
- `owner_prompt(entries, subscriptions, teams)`: the subscription table (team → services, and services with no owner), the team list with contacts, then the entries as JSON lines.
- `INVESTIGATOR_SYSTEM`, identical in every arm:
  > A scanner published one message per operational risk on a shared channel. Below are the records this platform kept about what happened next. For each risk, say whether it was handled (a team reported a fix) or lost, and if lost, where: no_subscriber (no team owns the service), missing_fields (the message lacked the fields needed to act), wrong_team (it reached a team that does not own the risk), consumer_failed (a consumer took it and never reported), consumer_declined (the right team declined it), unknown. Cite the message ids your answer rests on. Use only the records.
  > Reply with JSON only, matching: {"risks": [{"risk_id": "<id>", "status": "handled|lost", "cause": "none|no_subscriber|missing_fields|wrong_team|consumer_failed|consumer_declined|unknown", "evidence": ["<message id>"]}]}
- `investigator_prompt(published, subscriptions, records)`: the published list (`message_id`, `risk_id`, `service`), the subscription table, then the records block (see `bus_trace.records_for_investigator`).
- `INVESTIGATOR_SCHEMA`: exactly 11 items, `risk_id` enum of catalogue ids.

### `bus_owner.py`

- `owner_view(arm, bus) -> list[dict]`: `steward-agent`: every store event; `platform-owned`: the dead letters and invalid entries (with correlation id and history); `no-owner`: `[]`.
- `ask_owner(arm, *, backend, model, bus) -> (actions, tokens, served, error, prompt_chars)`: not called when the view is empty or the arm is `no-owner`.
- `execute(actions, *, bus, risk_of, run_index) -> list[Delivery requests]` and an `action_log`: `redeliver` (message must exist in the store or exceptions; re-queues a copy to `route(service)`), `route` (copy with `service` rewritten, queued to that service's team), `assign_owner` (adds the assignment, then queues copies of every message for that service that never reached a team), `return_to_producer` (publishes `valid_copy(risk_of[id])` through `publish`, so it passes the schema in the platform arm). Unknown ids or impossible actions are logged as `ignored` with the reason. Copies use `copy_for_retry` (trace context only in platform-owned).

### `bus_trace.py`

- `ground_truth(*, risk_of, faults, deliveries, check_result) -> dict[risk_id, dict]`: `fixed`, `processed_fixed` (some delivery of a message for this risk returned `fixed`), `cause` (for unfixed risks: `no_subscriber` for fleet-telemetry risks never assigned, else by fault kind for injected loss targets never processed fixed: `missing_fields`, `wrong_team`, `consumer_failed`; else `consumer_declined` if every delivery was declined; else `fix_did_not_hold` if a delivery said fixed; else `unknown`), `channel_caused` (cause in the first four).
- `fault_outcomes(truth, faults) -> dict[kind, "recovered"|"absorbed"|"lost"]` for the three loss kinds.
- `records_for_investigator(arm, bus, *, strip_trace=False) -> str`: `no-owner`: each team's local log; `steward-agent`: the store plus the steward's action log; `platform-owned`: the store, dead letters, invalid entries and the platform agent's action log. `strip_trace=True` removes `correlation_id` and `history` and replaces every copy's id with a fresh `c..` id, consistently across the block.
- `score_investigator(answer, truth) -> dict`: `channel_losses`, `correct` (cause matches on channel-caused losses), `accuracy`, `false_assurance_channel` (status handled on a channel-caused loss), `false_assurance_fix` (handled on `fix_did_not_hold`), `missing_risks` (ids the answer left out count as wrong).

### `bus.py`

`Delivery` dataclass: message_id, risk_id, team, service, pass_no (1 or 2), attempt, duplicate (bool), injected_failure (bool), outcome (`fixed|not_mine|cannot_fix|invalid|error|injected-failure`), reason, files_changed (paths), tokens, served_models, wall_seconds.

`run_bus(*, arm, run_index, backend, consumer_model, owner_model, workdir=None) -> dict`:

1. Scratch copy of the fixture (`CHECKER.md` excluded), `Bus(policy, SUBSCRIPTIONS)`.
2. Scanner messages for `run_index`; apply `malform`/`misroute` to the fault targets; publish all.
3. Pass 1: per team, process its queue in order (teams in parallel, one lock per service directory). For each message: if `idempotent_receiver` and the id is acked, skip and record. If it is the consumer-crash target's first delivery: `injected-failure`, no call; with `acks`, queue one retry copy; after a second failure, dead letter. Else call the consumer, write the files it returned, record the outcome; with `acks`, ack a valid reply; with `dead_letter`, send `not_mine`/`cannot_fix` to the Dead Letter Channel. After the duplicate target is first processed, the transport queues it again for the same team (the Idempotent Receiver stops it in platform-owned).
4. Owner round (steward-agent, platform-owned): `ask_owner`, `execute`, then pass 2 with the same delivery rules.
5. `check()` on the scratch repo; `ground_truth`; `fault_outcomes`.
6. Investigator call on `records_for_investigator(arm, bus)`; in platform-owned a second call with `strip_trace=True`.
7. Return the record.

Record (one JSONL row per run):

```
at, run, arm, backend, consumer_model, owner_model, code_commit,
faults {kind: risk_id}, wrong_destination, published [{message_id, risk_id, body, route}],
deliveries [Delivery], owner {called, actions, action_log, tokens, error, prompt_chars},
invalid, dead_letters, store_size,
score (checker, 03's score_record), truth, fault_outcomes, fleet_fixed (0-2), clean_fixed, clean_total (5),
duplicate {calls, rewrote, harm}, wrong_service {declined, bogus_edit},
investigator {answer, score}, investigator_no_trace {answer, score} (platform-owned, else null),
tokens_by_role {consumers, owner, investigator, investigator_no_trace}, total_tokens (consumers + owner + investigator),
consumer_calls, consumer_served_primary, owner_served_primary, wall_seconds
```

Clean targets: routable risks that are neither a loss-fault target nor the duplicate target. `duplicate.harm`: the second processing changed files and the target is not fixed at the end.

### `run.py`

One run of one arm (`--arm`, `--run-index` for the fault schedule, default 0), printed: faults, each delivery, owner actions, fixed, fault outcomes, investigator accuracy, tokens. Flags as 03's `run.py`. Refuses `--out` equal to `results/runs.jsonl`.

### `experiment.py`

- `refuse_unregistered()` as in plan 04 with `HEADING = "## 05-event-bus:"` and the same draft file.
- `run_arms(n, arms, out, ..., parallel_arms, token_ceiling)`: for each index, the arms in `rotated(arms, index)` order (or in parallel threads), `run_index = index`.
- `summarise(path)`, refusing mixed setups, four tables:
  1. per arm: n; risks fixed mean (min); clean fix rate; fleet-telemetry fixed (of 2n); outside contract; regressions; tokens mean (consumers / owner / investigator); consumer calls mean; owner prompt chars mean; wall mean.
  2. faults: rows = arm × kind (malformed, wrong-service, consumer-crash), columns recovered / absorbed / lost (of n); duplicate calls, rewrites, harm; wrong-team declines and bogus edits.
  3. traceability: per arm, attribution accuracy (sum correct / sum channel losses), false assurance (channel) and (fix); for platform-owned, the same without trace context.
  4. per run: faults and each arm's fault outcomes and fixed count.
  Footer: models, served models, backend, run dates.
- Flags: `--n` (10), `--arms`, `--parallel-arms`, `--token-ceiling`, `--out`, `--summary`, `--backend`, `--consumer-model`, `--owner-model`, `--summarise-only`.

### `README.md` (skeleton)

Headings as 03: `# 05 · Event bus: who looks after the shared channel`; The question; The short answer (`TBD`); The experiment (scanner, subscriptions and the unowned service, consumers, the three arms as the mechanics table, the action set, the fault schedule, the investigator and ground truth, scoring by the shared checker; say why there is no separate `dropped` fault); Results (`TBD`); Predictions (`TBD`); Limits (one task, n=10, Claude only, harness applies edits, simulated in-process bus, the seven disclosed choices from `experiment-design.md`, platform guarantees are code, tokens are counts); Run it yourself; What's in the code.

## Tests (`05-event-bus/tests/`)

`bus_helpers.py`: `_isolate()` pattern from 03 with `MODULES = ("experiment", "run", "bus", "bus_config", "bus_faults", "bus_channel", "bus_prompts", "bus_owner", "bus_trace")`; a `Script` routing by system prompt (consumer by team name, steward, platform agent, investigator); `consumer_fix(repo, message)` that applies `tests/harbour_fixes.FIXES[risk_id]` to a copy and returns the changed files of the message's service.

| Test | Asserts |
|---|---|
| `test_subscriptions_from_pristine_owners` | 6 teams with services, fleet-telemetry in no subscription, `len(ROUTABLE) == 9` |
| `test_fault_schedule` | Four distinct targets per run; over runs 0-8 each kind hits all 9 routable risks; run 9 equals run 0; the schedule matches the table in `experiment-design.md` |
| `test_wrong_destination` | Different owner team, never fleet-telemetry, deterministic |
| `test_schema` | Every scanner message passes; a malformed one fails; a misrouted one passes |
| `test_no_owner_mechanics` | Unroutable discarded, crash target never processed, duplicate processed twice, declines go nowhere, team logs hold receipts and outcomes, no store |
| `test_platform_mechanics` | Malformed to Invalid Message Channel; crash target retried once with the same id and a history hop; duplicate skipped by the Idempotent Receiver; not_mine and no-subscriber to the Dead Letter Channel |
| `test_owner_views` | Steward prompt holds every store event; platform agent prompt holds only exception entries; no owner call in no-owner |
| `test_actions` | `route` rewrites service and reaches the right team; `assign_owner` delivers both fleet-telemetry messages to the team; `return_to_producer` republishes a schema-valid body; unknown ids logged as ignored; copies carry correlation only in platform-owned |
| `test_ground_truth_and_fault_outcomes` | One case per cause; absorbed when a sibling message's consumer fixed the target |
| `test_investigator_scoring` | Correct cause, wrong cause, missing risk, false assurance (channel and fix) |
| `test_strip_trace` | No `correlation_id` or `history` left; copy ids replaced consistently |
| `test_checker_answer_key_not_in_scratch` | No `CHECKER.md` in the scratch copy |
| `test_end_to_end_owner_decides_what_is_lost` | The deterministic proof of the folder's value. Run index 0, scripted consumers fix the reported risk with the reference fix (and reply `not_mine` when the file is not under the message's service, `cannot_fix` when fields are missing). `no-owner`: fleet-telemetry 0 of 2, crash target lost, wrong-service target lost, malformed target lost, duplicate processed twice. `platform-owned` with a scripted platform agent that assigns fleet-telemetry to Team Fleet, routes the declined message to the file's service and returns the malformed one: 11 of 11 fixed, 0 regressions, crash target recovered, duplicate processed once. `steward-agent` with the same scripted actions: same fixes, duplicate processed twice, copies without correlation. A scripted investigator that answers from the records scores higher accuracy in platform-owned than in no-owner. |
| `test_experiment_refuses_draft` | As in plan 04 |
| `test_summary_refuses_mixed_setups` and `test_summary_renders` | On hand-built rows |
| `test_run_refuses_benchmark_file` | As in 03 |

## Definition of done

1. `uv run pytest` passes: 305 existing tests plus the new ones, no model calls. Quote the final line.
2. `git diff --stat` shows changes only under `05-event-bus/`.
3. `uv run 05-event-bus/run.py --arm <arm> --out ~/.cache/issue15/05-scratch.jsonl` completes one real run for each of the three arms (about 2 to 3 min each); every served model matches the requested one. Quote one summary line per arm.
4. `uv run 05-event-bus/experiment.py --n 1` exits with the DRAFT refusal. Quote it.
5. README skeleton present with every section above.

If you run short of time, cut in this order and say so: the `investigator_no_trace` call (keep the field, set it to null), then table 4 of the summary. Never cut a fault kind or an arm.

## Commands for the README

```bash
uv sync
uv run pytest                                                                       # no model calls
uv run 05-event-bus/run.py --arm platform-owned --out ~/.cache/issue15/05-scratch.jsonl
uv run 05-event-bus/run.py --arm no-owner --run-index 3 --out ~/.cache/issue15/05-scratch.jsonl
uv run 05-event-bus/experiment.py                                                   # 3 arms, n=10, results/runs.jsonl
uv run 05-event-bus/experiment.py --parallel-arms
uv run 05-event-bus/experiment.py --summarise-only
```
