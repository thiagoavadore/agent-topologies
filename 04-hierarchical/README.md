# 04 · Hierarchical: leads managing leads

## The question

Should the agent layers match the team layers? Put middle managers between the workers and the platform lead, cut the tree along team boundaries or along technology, and see where a coupling between services gets resolved, or lost.

## The short answer

TODO: fill after the benchmark (`results/runs.jsonl`). One paragraph per finding, each with the number from this folder's own runs.

## The experiment

The shared world is Harbour Bikes as real files: [`fixtures/harbour-bikes/`](../fixtures/harbour-bikes/) (eight services, eleven planted risks, five numeric needs, one hub file `platform.yaml`). It is the same world, answer key and checker as folders 02 and 03. This folder changes the management structure and nothing else.

- **The task:** every service gets one worker (Claude Sonnet 5.5) that fixes the risks in its service and, where a fix needs a platform default, sends a hub request `{key, value, reason}`. Workers have no tools. They get [`CONTRACT.md`](../fixtures/harbour-bikes/CONTRACT.md), the mandate line, `platform.yaml`, `teams.yaml`, `libs/platform_config.py` and every file of their own service. `CHECKER.md` is never copied into a scratch repo.
- **The mandate is inherited from 03's mandated rung.** The timeout in `charge()` or `send_email()` must be `http_timeout("<service>")`. A file that breaks it is reverted (its other files are kept), any `overrides:` a worker writes is stripped, and only the platform lead may grant an override. 03 showed that with a local route open nobody touches the hub, so the hub has to be the only seam for the management layers to matter.
- **The platform lead** (Claude Opus 5.5) owns `platform.yaml`. It decides the final value of every key it is asked about and may grant a service its own override of an HTTP key. It is not called when no request reaches it.
- **Sub-leads** (Claude Opus 5.5, three per tiered arm) each lead a group of services. A sub-lead sees `platform.yaml`, the `service.yaml` (with `needs`) of every service in its group, its workers' requests and notes, and the rules the workers followed. It sends the platform lead a list of requests: it may forward, merge, change or drop its workers' requests, or add its own. Requests only: no free-text note. Whether a service should get its own override is the platform lead's call: it sees each request's reason and may grant one.
- **The harness applies everything** to a fresh copy of the world per arm: every worker's files first, then the platform lead's override grants and decided hub values. Score: [`fixcheck.check()`](../src/topologies/fixcheck.py), rules in [`CHECKER.md`](../fixtures/harbour-bikes/CHECKER.md). There is no folder-local fix rule and no model judging.
- **Every agent that decides gets the rules it decides under:** the mandate line, the override rule and `CONTRACT.md` go to workers, sub-leads and the platform lead, so no tier is judged against a contract it never saw.

### Four arms, one set of worker replies

| Arm | Middle tier | What the platform lead sees | Why it is here |
|---|---|---|---|
| `flat` | none: the lead manages the 8 workers | every worker request, and every service's needs | Control: what the middle tier costs and what it loses in information |
| `by-team` | 3 sub-leads along team boundaries | sub-lead requests and the group list | Agent layers match team layers |
| `by-runtime` | 3 sub-leads by language | sub-lead requests and the group list | The component-team cut: splits Team Rentals and Team Fleet and both of bookings-api's call edges |
| `by-runtime-registry` | the same sub-lead replies as `by-runtime`, reused | sub-lead requests, the group list and the needs registry | A published Team API: does it substitute for an aligned cut? |

| Group | `by-team` | `by-runtime` |
|---|---|---|
| 1 | `rentals-and-money`: bookings-api, pricing-engine, payments-gateway | `python`: bookings-api, notifications, maintenance-scheduler |
| 2 | `fleet`: fleet-telemetry, maintenance-scheduler | `jvm-and-go`: customer-profiles, payments-gateway |
| 3 | `customer-and-back-office`: notifications, customer-profiles, admin-console | `node-ruby-rust`: pricing-engine, admin-console, fleet-telemetry |

Owner teams come from each pristine `service.yaml` (`owner.team`): Team Rentals owns bookings-api and pricing-engine, Team Money payments-gateway, Team Growth notifications, Team Identity customer-profiles, Team Fleet maintenance-scheduler, Team Ops Tools admin-console. fleet-telemetry has `owner: null` (the planted risk).

**Paired design.** Per run index the 8 workers run once, the `by-team` sub-leads run once, the `by-runtime` sub-leads run once and serve both `by-runtime` and `by-runtime-registry`, then each arm gets its platform-lead call. Worker files are identical across the four rows of an index, so differences in risks fixed and regressions come only from hub decisions and override grants. The order of the management calls rotates per index.

### The traps and the metrics

bookings-api calls pricing-engine (waits 400 ms, so pricing needs a timeout of at most 300 ms) and payments-gateway (charges at up to 20/s, so payments needs a rate limit of at least 20/s). Both regression needs are reached only through hub keys. bookings-api and notifications want a longer timeout than 300 ms; a hub raise breaks pricing-engine.

| Metric | Definition |
|---|---|
| Risks fixed of 11, regressions, outside contract | `fixcheck.check()` on the arm's repo |
| Regressions by need | `pricing-engine.demand_model_timeout`, `payments-gateway.inbound_rate_per_caller` |
| Escalations | Requests that reach the platform lead, in total and hub-changing (the value differs from the current hub value) |
| Harmful requests | A request (key, value) harms a regression need when that need reads the key and the value, applied with no override, breaks the need's bound. The value alone is classified: intent in the reason text is not read, so a value that would break a need counts as harmful even if the reason asks for an override (parsed with the checker's own `parse_duration` / `parse_rate`). Counted for worker requests and for requests reaching the platform lead, split inside (the harmed service is in the requester's subtree) or outside |
| Coupling ladder, per regression need | `not-threatened`, `absorbed-at-sub-lead` (a worker request harms it, nothing reaching the lead does), `caught-at-top` (a request reaching the lead harms it, the lead answered and the need holds), `top-failed` (same, but the lead's call failed, so the hub stayed as it was), `reached-hub` (regressed after a harmful request reached the lead), `introduced-by-top` (regressed with no harmful request reaching the lead), `introduced-by-workers` (already regressed in a repo with only the workers' files applied) |
| Overrides granted, distinct effective timeouts | As in 03 |
| Tokens by tier, management tokens | Workers (shared), sub-leads, platform lead; management = sub-leads + platform lead |
| Platform-lead prompt size | Characters of the platform lead's prompt: span-of-control load, deterministic |

All of these are computed by code from the run records. Nothing is judged by a model and nothing is read out of prose.

### Design choices that create the effect (disclosed)

1. The mandate makes the hub the only seam for timeouts (03 showed local routes dissolve contention).
2. The upward protocol is requests only. A sub-lead whose services need nothing changed sends nothing, so a standing constraint (pricing's 300 ms) travels up only if a sub-lead that holds pricing also holds a service asking to change the timeout. The registry arm tests the alternative.
3. The runtime cut puts both services that want a longer timeout (bookings-api, notifications) under one sub-lead and away from pricing-engine and payments-gateway. Any cut that separates bookings-api from the services it calls would do the same: the result is about cutting the call graph, not about languages.
4. `by-team` does not contain the notifications-to-pricing coupling: notifications (Team Growth) and pricing (Team Rentals) share only the hub. A hub default couples every service, so no team cut fully contains it.
5. The flat lead sees all needs because it manages all services directly; the tiered lead does not. That is the information rule, the same in every arm: a lead sees the `service.yaml` of the services it manages directly. The tiered lead is not blind to the coupling though: `CONTRACT.md` rule 8, which every lead receives, says that `pricing-engine/src/index.js` reads `PLATFORM_HTTP_TIMEOUT` and that its timeout changes through `overrides:` or the hub. What it lacks is pricing's 300 ms bound.
6. The three groups are the authors' choice (teams grouped along the call graph, fleet-telemetry with Team Fleet).
7. The request schema has no way to ask for a service-scoped value, on purpose. A dry run on 11 Oct 2026 (index 5) gave workers and sub-leads an optional `override_for: <service>` field with a prompt line explaining it. Every request in every arm then came back service-only, no request changed the hub default, nothing regressed in any of the four arms, and the cut stopped mattering. Advertising a service-scoped channel reopens the local route that 03 showed dissolves contention, so the field was removed. The platform lead still sees each reason and may grant overrides itself. The headline metric is the checker's regressions, which wording cannot move. The raw dry-run rows are kept outside `results/`.

Predictions were written before the benchmark and frozen in [`PREDICTIONS.md`](../PREDICTIONS.md).

## Results

TODO: fill from `results/summary.md` after the benchmark. Per-arm table (risks fixed, runs with a regression and which need, escalations, harmful requests, overrides granted, distinct timeouts, tokens by tier, platform-lead prompt size), the coupling ladder per arm and need, and the per-run table of final hub values.

Raw data: [`results/summary.md`](results/summary.md) for the tables, [`results/runs.jsonl`](results/runs.jsonl) for every row (each tier's requests, the platform lead's decisions and the full score).

## Predictions

TODO: score each prediction from [`PREDICTIONS.md`](../PREDICTIONS.md) as hit or miss, with the number. Dry runs are disclosed there.

| Prediction | Result |
|---|---|
| TODO | TODO |

## Limits

- One task, n=10 per arm. Comparisons hold inside this folder only, never against 01, 02 or 03.
- Claude models only: Sonnet 5.5 workers, Opus 5.5 leads, one backend (`claude-cli`).
- The harness applies the edits, so this measures where a coupling is resolved, not how well agents edit files. Workers reply once with full file contents and have no tools.
- Everything in the traps list above is a design choice of the authors: the mandate, requests-only, the two cuts and the information rule. Pre-registering the checker and the predictions makes that visible, it does not remove it.
- The workers are paired: all four arms resolve one set of worker replies per index. That removes worker noise from the comparison and means the ten indexes are ten independent draws of workers, not forty.
- The flat lead sees the needs of all eight services and the tiered lead does not (the registry arm adds them back). Part of any `flat` advantage is that information, not the absence of a middle tier.
- Reasons are capped (workers 200 characters, sub-leads and the platform lead 400) and each system prompt states its cap. A sub-lead whose reply fails the schema twice forwards its workers' requests unchanged; the row records `fallback` and the summary counts them. A platform lead that fails twice leaves the hub as it was; the ladder then says `top-failed`, not `caught-at-top`.
- Hub requests that arrive with an unreadable value are counted as harmless, and flagged `unreadable` in the row. A blank platform-lead value is not applied and is listed in `unreadable_decisions`.
- Tokens are counts of everything sent and received, including Claude Code's fixed prompt per call, not cost. Worker tokens repeat in all four rows of an index and the `by-runtime-registry` row repeats the `by-runtime` sub-lead tokens, so do not sum tokens across rows.
- Management time is reported as summed call seconds (parallel sub-lead calls add up), not elapsed time, and the reused `by-runtime` sub-leads count in the `by-runtime` row only.
- Rows record the code commit, with `-dirty` when the code differs from it; the summary refuses dirty or mixed commits. Rows also list calls that errored and calls whose served model differed from the requested one.
- There is no failure injection here: partial failure is 03's question.
- `hier_hub.py` duplicates three small helpers from 03 so the folder runs on its own. Moving them into `src/topologies/` is logged debt.

## Run it yourself

```bash
uv sync
uv run pytest                                                            # no model calls
uv run 04-hierarchical/run.py --out ~/.cache/issue15/04-scratch.jsonl    # one paired run, all four arms
uv run 04-hierarchical/run.py --arms by-team by-runtime --out ~/.cache/issue15/04-scratch.jsonl
uv run 04-hierarchical/experiment.py                                     # n=10, results/runs.jsonl
uv run 04-hierarchical/experiment.py --parallel-arms                     # same, arms in parallel
uv run 04-hierarchical/experiment.py --summarise-only
```

`experiment.py` refuses to run until `docs/specs/spec-issue-15-04-05/predictions-draft.md` exists and no longer says DRAFT, and `PREDICTIONS.md` has a `## 04-hierarchical:` section. `run.py` is always allowed and refuses to write `results/runs.jsonl`. Keep one setup per `runs.jsonl`: the summary refuses a file that mixes models, served models or backends.

## What's in the code

| File | What it does |
|---|---|
| [`hier.py`](hier.py) | The workers, sub-leads and platform lead, applying their output to a fresh repo, scoring through the shared checker, the run record. |
| [`hier_prompts.py`](hier_prompts.py) | Every prompt and reply schema. |
| [`hier_harm.py`](hier_harm.py) | Which requests would break a regression need, inside or outside the requester's subtree, and the coupling ladder. |
| [`hier_hub.py`](hier_hub.py) | Reading and editing `platform.yaml`, `service.yaml` overrides, the mandate check (copied from 03). |
| [`hier_config.py`](hier_config.py) | The models, the arms, the two cuts, owner teams. |
| [`run.py`](run.py) | One paired run, printed. |
| [`experiment.py`](experiment.py) | The benchmark loop, the summary and the pre-registration guard. |
| [`tests/`](tests/) | Scripted-model tests: no model calls. |
