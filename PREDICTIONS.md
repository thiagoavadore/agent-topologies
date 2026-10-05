# Predictions for Issue 14 (folders 02 and 03)

Frozen 2026-10-05, before any benchmark run. Results that disagree are reported as misses, not edited away. Approved by Claude under Thiago's delegation (logged in `docs/specs/spec-issue-14-02-03/.memlog.md`).

**What we had seen before writing this.** Design dry runs, none of which count as results: 02, three single runs (one faulted `every-handoff`, two clean `none`); 03, eleven single runs across the design iterations, which led to the structural ladder below and to removing one planted risk from the answer key (see `fixtures/harbour-bikes/CHECKER.md`). Their raw rows are kept outside `results/`. Predictions for arms we ran in a dry run lean on what we saw there; that is said in the row.

Scoring: the shared checker (`src/topologies/fixcheck.py`, 11 planted risks) for 03; the 02 metrics in `docs/specs/spec-issue-14-02-03/experiment-design.md`. Directions are relative to the other arms in the same folder. Never compare across folders.

## 02-pipeline-checkpoint: when stage 3 fails, whose problem is it?

| Metric | `none` | `end-only` | `every-handoff` |
|---|---|---|---|
| Injected faults that reach production | Almost all. Nothing checks, and later stages cannot recover a dropped fact or re-attribute a moved one. | Most. The stage-4 gate sees a plan consistent with its faulted input. | Fewest. The stage-1 gate sees the files and the facts side by side, the only place a dropped or moved fact is visible (it caught the one faulted dry run). |
| Stage blamed for caught faults | n/a | Stage 4, the wrong owner (it inherited the fault from stage 1). | Mostly stage 1, the owning stage. |
| Tokens, total | Lowest | Slightly above `none` (one gate call) | Highest; gates take roughly a third to a half. |
| False rejections on clean runs | Zero by construction | Low | Highest: four gates each get a chance to reject. |
| Recall on clean runs that reach stage 4 (the fair comparison) | About equal across arms, near 11 of 11 per run. | About equal | About equal; gates do not add findings. |
| Recall on all runs that reach stage 4 | Lowest: faulted runs count and each loses its injected risk. | About equal to `none`. | Highest, but mostly because caught faulted runs stop before stage 4 (a selection effect, reported with its run count, not quoted as a gain). |

## 03-fan-out-partial-reducer: who is responsible for the merge?

The arms form a ladder. Each rung closes one route a worker could use to meet its own service's need without touching the shared `platform.yaml`.

| Metric | `overrides-allowed` | `code-local` | mandated `first-wins` | mandated `supervisor-merges` | mandated `hub-owner` | mandated `human-decides` |
|---|---|---|---|---|---|---|
| Hub conflicts per run | About zero: workers fork with local overrides (seen in dry runs). | Low, rate limit only: timeouts go into code literals (seen). | 1 to 2: `http.default_timeout` (W1 vs W2) in most runs, rate limit in some. | Same as mandated `first-wins` (identical workers before the merge). | Zero textual; the same disagreements arrive as competing change requests. | Same as mandated `first-wins`. |
| Distinct effective timeouts across services | Highest, 3 or more: the platform forks. | 2 or more. | 1 to 2. | 2: the shared value plus pricing-engine's granted override. | 2, as `supervisor-merges`. | 2 if the human grants pricing an override, else 1. |
| Regressions | Low: each service overrides for itself, nobody raises the hub. | Some: an uncontested hub timeout above 300 ms silently breaks pricing-engine (seen once). | Highest: nobody can grant pricing-engine an override, so any hub timeout that serves bookings-api breaks it in nearly every run. | Near zero: the supervisor sees every service's needs and grants pricing-engine an override (seen in both dry runs). | Near zero, as `supervisor-merges`. | Low; depends on whether the human checks pricing-engine's need before picking. |
| Risks fixed (of 11) | High | High | Lower than the owned mandated arms when a dropped hub value leaves a need unmet. | Highest | Highest, about equal to `supervisor-merges` | About equal to `supervisor-merges` |
| Mandate rejections | n/a | n/a | Rare | Rare | Rare | Rare |
| Unfixed services named in the closing merge report (runs with a failed worker) | Named in nearly every run of every arm: the report is told which workers returned nothing. Arms differ on risks the merge itself left open, which the report is likelier to miss in the unowned arms. | | | | | |
| Tokens | Lowest | Low | Workers plus the report | Plus one Opus call per conflict | Highest: every hub change through Opus | Mandated `first-wins` plus the supervisor's shadow pick |
| Human seconds per conflict | n/a | n/a | n/a | n/a | n/a | Tens of seconds; the human agrees with the supervisor's shadow pick in most but not all conflicts. |

## What would surprise us

- 02: `every-handoff` fails to beat `end-only` on faults reaching production. That would mean the gate design, not its position, is the problem.
- 03: mandated `first-wins` shows no more regressions than mandated `supervisor-merges`. That would mean ownership of the merge does not matter once the hub is the only seam.
- 03: `overrides-allowed` shows conflicts comparable to the mandated arms. That would undo the ladder's premise that contention comes from the architecture.
