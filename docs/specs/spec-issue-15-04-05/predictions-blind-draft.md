## 04-hierarchical, blind leads: does the cut matter once no lead is told about pricing?

Frozen 2026-10-12 at commit 9fc08a7, before any blind benchmark run. Approved by Thiago on 2026-10-12 ("go ahead, run it when ready").

**Why this run.** In the first 04 benchmark (0db1298, 40 rows) no arm regressed. Every lead's prompt carried CONTRACT.md rule 8 ("Leave the line in pricing-engine/src/index.js that reads PLATFORM_HTTP_TIMEOUT"), the only text outside pricing-engine's own group that names it as a consumer of the hub timeout. In `by-runtime` the platform lead named pricing-engine in 9 of 10 reasons although no request that reached it mentioned pricing. The blind run removes rule 8 from every sub-lead and platform-lead prompt. Workers keep the full contract (they edit files; leads do not), and their replies are replayed from the published run, so each blind index pairs with the published index: same worker files and requests, same arm order. `flat` keeps every service's needs and `by-runtime-registry` keeps the registry, by design.

**What we had seen before writing this.** The published 04 results, and the reviewer's diagnostic count over their stored reasons (regex over prose, not a metric): `by-runtime` platform-lead reasons cited PLATFORM_HTTP_TIMEOUT in 9 of 10 and also argued that other services' needs were not stated in 8 of 10; the python sub-lead offered the override route in 9 of 10. One blind dry run on uncommitted code later committed as 9fc08a7 (`~/.cache/issue15/04-blind-r3.jsonl`, index 3, all four arms, workers replayed): every arm kept 300ms, no regressions, pricing `caught-at-top` in all four, same as published index 3. The python sub-lead still sent a harmful 15s request and the platform lead granted overrides instead.

**Decision rule, set before the run.** Lead sampling noise on a 0 of 10 baseline is real. We call it an effect only if `by-runtime` blind regresses in 3 or more of 10 while `by-team` blind stays at 0 to 1.

| Metric | `flat` | `by-team` | `by-runtime` | `by-runtime-registry` |
|---|---|---|---|---|
| Runs with a regression (of 10) | 0 to 1. Needs are in its prompt. | 0 to 1. rentals-and-money carried pricing's bound up in 10 of 10 published runs, a channel the blind contract does not touch. | 0 to 3, most likely 0 to 1. The top also held back because needs were unstated, and the python sub-lead offered overrides. Only at 3 or more do we say rule 8 was doing the protecting. | 0 to 1 |
| Final hub `http.default_timeout` = 300ms (of 10) | 10 | 10 | 7 to 10 | 9 to 10 (the published run had 9) |
| Pricing ladder | `caught-at-top` | `caught-at-top` | `caught-at-top`, plus `reached-hub` in runs that regress | `caught-at-top` |
| Paired flips against the published run (indexes whose final hub timeout differs) | 0 to 1 | 0 to 1 | 0 to 3 | 0 to 1 |
| Overrides granted, mean | about 2.8, as published | about 2.8 | about 3, as published | about 2.8 |
| Risks fixed | 11 in all: same worker files, and the top grants the timeout overrides either way | | | |
| Management tokens | within 5% of the published run (rule 8 is about 200 characters) | | | |

**What would surprise us.** `by-runtime` blind at 4 or more regressions: the published 0 of 10 would then be an artefact of the contract, and the 04 answer needs that caveat up front. Or `by-team` blind regressing: rentals-and-money's reasons would not be enough on their own.
