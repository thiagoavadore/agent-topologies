# Predictions for Issue 14 (folders 02 and 03)

> **DRAFT, awaiting Thiago's approval.** Written 2026-10-04, before any round-2 run exists. Once approved and committed, these stay as written: results that disagree are reported as misses, not edited away. Scoring is the shared checker (`src/topologies/fixcheck.py`, rules in `fixtures/harbour-bikes/CHECKER.md`) for 03 and the 02 metrics in `docs/specs/spec-issue-14-02-03/experiment-design.md`.

Directions are relative to the other arms in the same folder. Never compare across folders.

## 02-pipeline-checkpoint: when stage 3 fails, whose problem is it?

| Metric | `none` | `end-only` | `every-handoff` |
|---|---|---|---|
| Injected faults that reach production | Almost all. Nothing checks, and later stages cannot recover a dropped fact or re-attribute a moved one. | Most. The stage-4 gate sees a plan that is consistent with its faulted input, so the fault looks clean by then. | Fewest. The stage-1 gate sees the repo files and the facts side by side, which is the only place a dropped or moved fact is visible. |
| Stage where caught faults are caught | n/a | Stage 4, so the failure is logged against the wrong owner (stage 4 inherited it from stage 1). | Mostly stage 1, the owning stage. |
| Tokens, total | Lowest | Slightly above `none` (one gate call) | Highest; gates take roughly a third to a half of all tokens. |
| False rejections on clean runs | Zero by construction | Low | Highest, because four gates each get a chance to reject. |
| Evidence-grounded recall (final plan), on runs that reach stage 4 | Every run reaches stage 4, so faulted runs count and each loses its injected risk: lowest. | About equal to `none`: its only checkpoint comes after stage 4, so every run reaches stage 4 here too. | Highest, because a checkpoint failure stops the run and caught faulted runs never reach stage 4; the runs left are mostly clean. Measured on the fewest runs (false rejections also stop clean runs), so the run count is reported beside it. |

## 03-fan-out-partial-reducer: who is responsible for the merge?

| Metric | `first-wins` | `supervisor-merges` | `hub-owner` | `human-decides` |
|---|---|---|---|---|
| Hub conflicts per run | Same as the other edit arms (worker behaviour is identical before the merge), on the four hub keys. `http.default_timeout` gives the conflicts that matter: W1 (bookings-api, needs 12 to 25 s) and W2 (notifications, needs 2 to 20 s) both want it. `backup.policy` and `availability.min_instances` (W1 and W2 again) collide often, but usually on compatible values. `http.default_rate_limit` is wanted by W1 alone, so it conflicts rarely. | Same as `first-wins` | Zero textual conflicts in `platform.yaml`; the same disagreements arrive as competing change requests | Same as `first-wins` |
| Regressions | Highest. Whichever worker wins usually raised `http.default_timeout` well above 300 ms, so pricing-engine breaks; a global rate limit below 20/s breaks payments-gateway. | Lower than `first-wins`, not zero. Opus sees the two values and reasons but not pricing-engine's need unless a worker mentions it. | Lowest of the automated arms. One reducer sees every request together and is the most likely to push the change into a service override. | Low, but depends on whether the human checks the other services' needs; that costs seconds per conflict. |
| Risks fixed (of 12) | Lowest. The losing worker's hub value is dropped, so its timeout or rate need goes unmet. Service-local risks (secret, owner, exposure) are fixed in every arm about equally. | Higher than `first-wins` | Highest or equal to `supervisor-merges` | About equal to `supervisor-merges` |
| Risks outside contract (per `CONTRACT.md`) | Few, and about equal across the edit arms, since workers get the same contract and prompt. | Same as `first-wins` | Same, except hub changes the reducer writes can add their own | Same as `first-wins` |
| Unfixed services named in the closing Opus merge report, in runs with a failed worker | Every arm ends with the same report, which is told which workers returned nothing, so the failed worker's services are named in nearly every run of every arm. The arms differ on services left unfixed by the merge itself: here a dropped hub value leaves a working worker's risk open, and the report is most likely to call that service fixed. | Close to `hub-owner`: the report can see which conflicts were merged. | Highest: the reducer applied every hub change itself, so the report knows what was and was not applied. | About equal to `supervisor-merges` |
| Tokens (every arm includes the merge report call) | Lowest: workers plus the report | Workers, the report, and one Opus call per conflict | Highest: workers, the report, and every hub change through Opus | Same as `first-wins`, plus the supervisor's shadow pick per conflict |
| Wall seconds | Lowest | Middle | Middle to high | Highest, dominated by the human |
| Human seconds per conflict | n/a | n/a | n/a | Tens of seconds per conflict; the human picks what the supervisor picked in most but not all conflicts. |

## What would surprise us

- `every-handoff` fails to beat `end-only` on faults reaching production: that would mean the stage-1 gate cannot see the fault either, and the gate design, not its position, is the problem.
- `first-wins` scores no more regressions than `supervisor-merges`: that would mean workers prefer service overrides over hub edits, and the hub is less contended than designed.
- Zero regressions in every arm: the traps did not bite, and the 03 result says less about merge ownership than intended.
