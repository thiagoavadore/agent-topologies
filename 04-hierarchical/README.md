# 04 · Hierarchical: leads managing leads

## The question

Should the agent layers match the team layers? Put middle managers between the workers and the platform lead, cut the tree along team boundaries or along technology, and see where a coupling between services gets resolved, or lost.

## The short answer

On the first run the cut did not matter: nothing regressed in any arm (0 of 10 each), all 11 risks were fixed in all 40 arm-runs and the platform lead kept 300 ms in 39 of 40, but every lead had been told by `CONTRACT.md` rule 8 that pricing-engine reads the hub timeout. The layers added cost, not safety: management tokens were 3,947 for `flat`, 23,018 for `by-team` (about 5.8x) and 17,681 for `by-runtime` (about 4.5x). A second run took rule 8 out of every lead's prompt (worker replies replayed, so the runs are paired) and only `by-runtime` broke anything: 2 of 10 runs (6 and 8), where the hub went to 15s and pricing-engine regressed, against 0 of 10 for `flat`, `by-team` and `by-runtime-registry`. By our pre-registered rule (3 or more of 10 for `by-runtime` while `by-team` stays at 0 or 1) that is not called an effect, but it is the only arm in 80 arm-runs that broke anything, and it is the cut that separates pricing-engine from the services that wanted the raise.

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

Run on 11 Oct 2026, n=10 per arm, on freeze commit `0db1298` (all 40 rows recorded that commit, clean). Tokens count everything sent and received. Worker tokens are the same in all four rows of a run, so management tokens are the column to compare.

| Arm | Risks fixed of 11 (mean) | Runs with a regression | Escalations (hub-changing) | Harmful requests up (inside / outside) | Overrides granted (mean) | Distinct effective timeouts | Management tokens (sub-leads / lead) | Lead prompt chars |
|---|---|---|---|---|---|---|---|---|
| `flat` | 11.0 | 0 of 10 | 3.2 (3.2) | 2.1 (0.0 / 2.1) | 2.8 | 3.0 | 3,947 (0 / 3,947) | 5,484 |
| `by-team` | 11.0 | 0 of 10 | 3.0 (2.4) | 1.4 (0.4 / 1.0) | 2.8 | 3.0 | 23,018 (19,325 / 3,692) | 5,215 |
| `by-runtime` | 11.0 | 0 of 10 | 2.3 (2.0) | 1.0 (0.0 / 1.0) | 3.0 | 2.2 | 17,681 (14,089 / 3,592) | 5,022 |
| `by-runtime-registry` | 11.0 | 0 of 10 | 2.3 (2.0) | 1.0 (0.0 / 1.0) | 2.8 | 2.3 | 17,973 (14,089 / 3,884) | 5,768 |

Coupling ladder, runs per outcome:

| Arm | pricing-engine | payments-gateway |
|---|---|---|
| `flat` | 10 `caught-at-top` | 9 `not-threatened`, 1 `caught-at-top` |
| `by-team` | 10 `caught-at-top` | 9 `not-threatened`, 1 `absorbed-at-sub-lead` |
| `by-runtime` | 10 `caught-at-top` | 9 `not-threatened`, 1 `absorbed-at-sub-lead` |
| `by-runtime-registry` | 10 `caught-at-top` | 9 `not-threatened`, 1 `absorbed-at-sub-lead` |

Reliability: 0 platform-lead calls failed, 0 of 30 sub-lead calls in each tiered arm fell back to forwarding unchanged, 0 unreadable requests, 0 rows with a call error or a served-model mismatch.

What this tells us:

- **No cut regressed, so the cut did not separate the arms.** 0 of 10 everywhere and 11 of 11 fixed in every arm-run. There is nothing to rank on the safety columns.
- **The owner of the shared file saw every change.** The lead held the hub timeout at 300 ms in 39 of 40 arm-runs and caught every harmful timeout request (pricing-engine `caught-at-top` 10 of 10 in every arm). Sub-leads did not need to stop the threat, because the lead stopped it.
- **Sub-leads absorbed essentially nothing.** One payments-gateway threat was stopped below the top in each tiered arm, all in run 4. Everything else reached the lead.
- **The layers cost, and bought nothing measurable.** Management tokens: 3,947 for `flat`, 23,018 for `by-team` (about 5.8x), 17,681 for `by-runtime` (about 4.5x), 17,973 for `by-runtime-registry`. Summed call seconds tell the same story: 11 for `flat`, 45 for `by-team`, 36 for `by-runtime`.
- **The tiered lead was not overloaded.** Its prompt was about the same size as the flat lead's (5,022 to 5,768 characters against 5,484), so span of control did not show up here.
- **The registry did not matter either.** The one run where the hub went to 15s was `by-runtime-registry` run 7. The lead accepted a 15s request and in the same reply granted pricing-engine a 300 ms override, so pricing-engine kept 0.3s and nothing regressed. The registry arm has the needs list, but so did the lead in `flat` and it behaved the same way: the other 39 arm-runs kept 300 ms.
- **Fewer harmful requests reached the lead in the tiered arms.** Harmful requests reaching the lead: 2.1 for `flat`, 1.4 for `by-team` (0.4 inside the harmed subtree), 1.0 for `by-runtime`. Sub-leads merged or dropped some, and the lead still caught what remained.

Raw data: [`results/summary.md`](results/summary.md) for the tables, [`results/runs.jsonl`](results/runs.jsonl) for every row (each tier's requests, the platform lead's decisions and the full score).

## Predictions

Written before any run and frozen on 11 Oct 2026 in [`PREDICTIONS.md`](../PREDICTIONS.md) (commit `0db1298`). Results that disagree are reported as misses. The dry runs are disclosed there.

| Prediction | Result |
|---|---|
| Runs with a regression: `flat` 0 to 1, `by-team` 0 to 2, `by-runtime-registry` 0 to 1 | **Hit.** 0 of 10 in each. |
| Runs with a regression: `by-runtime` highest, 1 to 4 | **Miss.** 0 of 10. The prediction itself named this as the one most likely to miss: the platform lead held 300 ms on its own. |
| pricing-engine regressed only where a regression happens; payments-gateway 0 in every arm | **Hit.** 0 of 10 for both needs in every arm. Final hub rates were 20/s, 30/s and 50/s, all inside the need. |
| Coupling ladder for pricing-engine: mostly `caught-at-top` in every arm, `absorbed-at-sub-lead` rare in `by-team`, `reached-hub` only where a run regresses | **Hit.** `caught-at-top` 10 of 10 in all four arms, 0 `absorbed-at-sub-lead`, 0 `reached-hub`. |
| Harmful requests up: `flat` about 2 all outside, `by-team` 1 to 2 mostly outside, `by-runtime` about 1 outside, registry same as `by-runtime` | **Hit.** 2.1 (0.0 / 2.1), 1.4 (0.4 / 1.0), 1.0 (0.0 / 1.0), 1.0 (0.0 / 1.0). |
| Escalations: `flat` highest, about 3 to 4; `by-team` about 3, fewer hub-changing; `by-runtime` about 2 to 3; registry same | **Hit.** 3.2 (3.2), 3.0 (2.4), 2.3 (2.0), 2.3 (2.0). |
| Risks fixed of 11: about equal, within a point | **Hit.** 11.0 in every arm, min 11. |
| Management tokens: `flat` lowest (about 4k), `by-team` 5 to 6 times `flat`, registry slightly above `by-runtime` | **Hit.** 3,947; 23,018 (about 5.8x); 17,973 against 17,681. |
| Management tokens: `by-runtime` about 3 to 4 times `flat` | **Miss.** About 4.5x (17,681). |
| Platform-lead prompt chars: `flat` largest, about equal to registry; `by-team` and `by-runtime` smaller | **Mixed.** The registry arm is the largest (5,768), `flat` is second (5,484), `by-team` 5,215 and `by-runtime` 5,022 are smaller. The sizes are within about 15% of each other. |
| Reliability: lead failures, sub-lead fallbacks and unreadable requests near zero | **Hit.** All zero. |

Score: 8 hits, 2 misses, 1 mixed, over the 11 rows above.

The surprises we named: `by-runtime` regressing no more than `by-team` happened (0 and 0), and so did the registry arm regressing as often as `by-runtime` (0 and 0), but both are the same finding: nothing regressed anywhere, so no comparison is possible. `absorbed-at-sub-lead` being common in `by-team` did not happen (0 of 10 for pricing-engine, 1 of 10 for payments-gateway). The honest reading of the "likeliest miss" is that rule 8 plus the lead's own caution protected pricing-engine whatever the cut, which is the limit below.

## Blind-lead variant (added 12 Oct 2026, after the benchmark)

A harness change made after the first benchmark, so it is dated and kept apart: it adds a mode, it does not change `full`. `--lead-contract blind` removes CONTRACT rule 8 (the line that says `pricing-engine/src/index.js` reads `PLATFORM_HTTP_TIMEOUT`) from the sub-lead system prompt and the platform-lead prompt. Workers keep the full contract. With `--lead-contract full`, the default, every prompt renders byte for byte as in the frozen benchmark (a hash test pins it). `flat` and `by-runtime-registry` still show the needs by design.

`--replay-workers 04-hierarchical/results/runs.jsonl` reuses the published worker replies, one set per run index, with no worker call. Each blind index therefore pairs with the published index: same worker files and requests, same arm order. The source must be one code commit and one worker model, and it must hold every index asked for. Rows carry `lead_contract` and `workers_replayed_from`, land in `results/blind/runs.jsonl` and `results/blind/summary.md`, and the summary refuses a file that mixes contracts. The blind run has its own pre-registration (`predictions-blind-draft.md`, then a `## 04-hierarchical, blind leads:` section in `PREDICTIONS.md`). Results: [Blind leads (second run)](#blind-leads-second-run-12-oct-2026) below.

## Blind leads (second run, 12 Oct 2026)

Run on 12 Oct 2026, n=10 per arm, on code commit `894edf0` (the commit that froze the blind predictions; all 40 rows recorded it). Leads got the contract without rule 8, workers were replayed from the published run, so run index and worker files pair one to one. Tokens count everything sent and received. Data: [`results/blind/summary.md`](results/blind/summary.md) and [`results/blind/runs.jsonl`](results/blind/runs.jsonl). The comparison below is [`results/blind/compare.md`](results/blind/compare.md), written by `experiment.py --lead-contract blind --summarise-only --out results/blind/runs.jsonl --summary results/blind/summary.md --compare-to results/runs.jsonl`, keyed by (run, arm).

| Arm | Runs with a regression, blind (published) | Final hub timeout 300 ms, blind (published) | Paired flips (runs whose final hub timeout differs) | Management tokens, blind (published) | Ratio |
|---|---|---|---|---|---|
| `flat` | 0 of 10 (0 of 10) | 10 of 10 (10 of 10) | 0 | 3,840 (3,947) | 0.97 |
| `by-team` | 0 of 10 (0 of 10) | 10 of 10 (10 of 10) | 0 | 22,765 (23,018) | 0.99 |
| `by-runtime` | 2 of 10 (0 of 10) | 8 of 10 (10 of 10) | 2 (runs 6, 8) | 16,085 (17,681) | 0.91 |
| `by-runtime-registry` | 0 of 10 (0 of 10) | 9 of 10 (9 of 10) | 2 (runs 6, 7) | 16,329 (17,973) | 0.91 |

Pricing-engine ladder, blind: `caught-at-top` in 10 of 10 runs for `flat`, `by-team` and `by-runtime-registry`; for `by-runtime` 8 `caught-at-top` and 2 `reached-hub`. payments-gateway: nothing regressed, 1 `absorbed-at-sub-lead` in each tiered arm, as before. Risks fixed: 11.0 in every arm, minimum 11. Overrides granted, mean: 2.5, 3.0, 2.7, 2.8 (published 2.8, 2.8, 3.0, 2.8). 0 platform-lead calls failed and 0 sub-lead calls fell back in any arm.

What this tells us:

- **`by-runtime` regressed pricing-engine in 2 of 10 runs, runs 6 and 8.** In both the lead set the hub timeout to 15s (its reasons: both python services need longer, no other group objected) and pricing-engine kept the hub value, 15.0s. In the published run the same indexes held 300 ms.
- **By the rule we set first, this is not an effect.** We said we would call it one only if `by-runtime` regressed in 3 or more of 10 while `by-team` stayed at 0 or 1. It was 2 of 10, and `by-team` was 0 of 10. We do not call it an effect. We also do not call it noise: it is the only arm in 80 arm-runs (40 published, 40 blind) that broke anything, and `by-runtime` is the cut that puts pricing-engine in one group and bookings-api and notifications, the two services that wanted the raise, in another.
- **`by-team` held without rule 8.** 0 of 10, and the lead kept 300 ms in all 10. rentals-and-money keeps pricing-engine and its callers in one group, so pricing's bound travels up with the request, a channel the blind contract does not touch.
- **The registry arm raised the hub to 15s in run 6 and did not regress.** The lead accepted the 15s request and granted pricing-engine a 300 ms override in the same reply, as in published run 7 of this arm. Paired against the published run, it raised in run 6 and held in run 7, so the registry moves where the raise happens, and the override keeps the damage out.
- **Blind leads cost less.** Management tokens are 9% lower for `by-runtime` and the registry arm, 1 to 3% lower for the others. Rule 8 is about 200 characters, so the gap is mostly different lead replies, not the prompt.

Predictions, frozen on 12 Oct 2026 in [`PREDICTIONS.md`](../PREDICTIONS.md) (section "04-hierarchical, blind leads", commit `894edf0`), before any blind run:

| Prediction | Result |
|---|---|
| Runs with a regression: `flat` 0 to 1, `by-team` 0 to 1, `by-runtime` 0 to 3 (most likely 0 to 1), `by-runtime-registry` 0 to 1 | **Hit.** 0, 0, 2 and 0 of 10. The 2 is inside the stated range, above the "most likely" 0 to 1. |
| Final hub timeout 300 ms: `flat` 10, `by-team` 10, `by-runtime` 7 to 10, registry 9 to 10 | **Hit.** 10, 10, 8 and 9. |
| Pricing ladder: `caught-at-top` everywhere, plus `reached-hub` in `by-runtime` runs that regress | **Hit.** 10, 10, 8 plus 2 `reached-hub`, 10. |
| Paired flips against the published run: `flat` 0 to 1, `by-team` 0 to 1, `by-runtime` 0 to 3 | **Hit.** 0, 0 and 2. |
| Paired flips: `by-runtime-registry` 0 to 1 | **Miss.** 2 (runs 6 and 7). |
| Overrides granted, mean: about 2.8, 2.8, 3, 2.8 | **Hit.** 2.5, 3.0, 2.7, 2.8, all within 0.3. |
| Risks fixed: 11 in all | **Hit.** 11.0 and minimum 11 in every arm. |
| Management tokens within 5% of the published run: `flat`, `by-team` | **Hit.** Ratios 0.97 and 0.99. |
| Management tokens within 5% of the published run: `by-runtime`, `by-runtime-registry` | **Miss.** Ratios 0.91 and 0.91. |

Score: 7 hits, 2 misses over the 9 rows (22 hits and 3 misses over the 25 arm-level cells). What would surprise us: `by-runtime` blind at 4 or more regressions, or `by-team` blind regressing. Neither happened.

## Limits

- **Rule 8 (addressed by the blind run, with what remains).** In the first run `CONTRACT.md` rule 8 went to every lead and named pricing-engine as a reader of the hub timeout, so no tier was blind to the coupling and nothing regressed. The blind run removed it from every lead's prompt: `by-runtime` regressed in 2 of 10, the others in 0. What remains: n=10 per arm, one task, and the 2 of 10 is below our pre-registered threshold. `OVERRIDE_RULE`, which tells every lead that only the platform lead grants overrides, is in the blind prompts too and still hedges the top: the lead can grant pricing-engine an exception, and in the other arms it did. The blind run also keeps the needs in `flat` and the registry arm by design. This folder shows the cut can matter when no lead is told what the shared file reaches. It does not measure how often.
- Debt paid between the two runs: the hub, override and mandate helpers that `hier_hub.py` copied from 03 now live in [`src/topologies/hubfile.py`](../src/topologies/hubfile.py). The checker label fix made for 03 and 05 does not affect 04: outside contract is 0.0 in every arm in both runs.
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

## Run it yourself

```bash
uv sync
uv run pytest                                                            # no model calls
uv run 04-hierarchical/run.py --out ~/.cache/issue15/04-scratch.jsonl    # one paired run, all four arms
uv run 04-hierarchical/run.py --arms by-team by-runtime --out ~/.cache/issue15/04-scratch.jsonl
uv run 04-hierarchical/experiment.py                                     # n=10, results/runs.jsonl
uv run 04-hierarchical/experiment.py --parallel-arms                     # same, arms in parallel
uv run 04-hierarchical/experiment.py --summarise-only
uv run 04-hierarchical/run.py --run 3 --lead-contract blind --replay-workers 04-hierarchical/results/runs.jsonl --out ~/.cache/issue15/04-blind-scratch.jsonl
uv run 04-hierarchical/experiment.py --lead-contract blind --replay-workers 04-hierarchical/results/runs.jsonl   # results/blind/
```

`experiment.py` refuses to run until `docs/specs/spec-issue-15-04-05/predictions-draft.md` exists and no longer says DRAFT, and `PREDICTIONS.md` has a `## 04-hierarchical:` section. `run.py` is always allowed and refuses to write `results/runs.jsonl`. Keep one setup per `runs.jsonl`: the summary refuses a file that mixes models, served models or backends.

## What's in the code

| File | What it does |
|---|---|
| [`hier.py`](hier.py) | The workers, sub-leads and platform lead, applying their output to a fresh repo, scoring through the shared checker, the run record. |
| [`hier_prompts.py`](hier_prompts.py) | Every prompt and reply schema. |
| [`hier_harm.py`](hier_harm.py) | Which requests would break a regression need, inside or outside the requester's subtree, and the coupling ladder. |
| [`hier_hub.py`](hier_hub.py) | Re-exports the hub, override and mandate helpers shared with 03 from [`src/topologies/hubfile.py`](../src/topologies/hubfile.py). |
| [`hier_config.py`](hier_config.py) | The models, the arms, the two cuts, owner teams. |
| [`hier_replay.py`](hier_replay.py) | Loads the worker replies of a published run for `--replay-workers`. |
| [`run.py`](run.py) | One paired run, printed. |
| [`experiment.py`](experiment.py) | The benchmark loop, the summary and the pre-registration guard. |
| [`tests/`](tests/) | Scripted-model tests: no model calls. |
