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
| Evidence-grounded recall (final plan) | Lowest on faulted runs; on clean runs, about equal to the others | About equal to `none` | Highest on faulted runs, if a stage-1 rejection leads to a rerun; equal to the others if it only logs. |

## 03-fan-out-partial-reducer: who is responsible for the merge?

| Metric | `first-wins` | `supervisor-merges` | `hub-owner` | `human-decides` |
|---|---|---|---|---|
| Hub conflicts per run | Same as the other edit arms (worker behaviour is identical before the merge). Most on `http.default_timeout`, which W1 (bookings-api, needs 12 to 25 s) and W2 (notifications, needs 2 to 20 s) both want. | Same as `first-wins` | Zero textual conflicts in `platform.yaml`; the same disagreements arrive as competing change requests | Same as `first-wins` |
| Regressions | Highest. Whichever worker wins usually raised `http.default_timeout` well above 300 ms, so pricing-engine breaks; a global rate limit below 20/s breaks payments-gateway. | Lower than `first-wins`, not zero. Opus sees the two values and reasons but not pricing-engine's need unless a worker mentions it. | Lowest of the automated arms. One reducer sees every request together and is the most likely to push the change into a service override. | Low, but depends on whether the human checks the other services' needs; that costs seconds per conflict. |
| Risks fixed (of 12) | Lowest. The losing worker's hub value is dropped, so its timeout or rate need goes unmet. Service-local risks (secret, owner, exposure) are fixed in every arm about equally. | Higher than `first-wins` | Highest or equal to `supervisor-merges` | About equal to `supervisor-merges` |
| Risks outside contract (per `CONTRACT.md`) | Few, and about equal across the edit arms, since workers get the same contract and prompt. | Same as `first-wins` | Same, except hub changes the reducer writes can add their own | Same as `first-wins` |
| Unfixed services named, in runs with a failed worker | Only as well as the harness lists skipped work; no model writes a merge summary. | Named in most runs | Named in most runs | Named by the harness |
| Tokens | Lowest (workers only) | Workers plus one Opus call per conflict | Highest (every hub change goes through Opus) | Same as `first-wins`, plus the supervisor's shadow pick |
| Wall seconds | Lowest | Middle | Middle to high | Highest, dominated by the human |
| Human seconds per conflict | n/a | n/a | n/a | Tens of seconds per conflict; the human picks what the supervisor picked in most but not all conflicts. |

## What would surprise us

- `every-handoff` fails to beat `end-only` on faults reaching production: that would mean the stage-1 gate cannot see the fault either, and the gate design, not its position, is the problem.
- `first-wins` scores no more regressions than `supervisor-merges`: that would mean workers prefer service overrides over hub edits, and the hub is less contended than designed.
- Zero regressions in every arm: the traps did not bite, and the 03 result says less about merge ownership than intended.
