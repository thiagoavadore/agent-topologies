# 03 · Fan-out with a partial reducer

## The question

When several workers write to the same shared file at the same time, who is responsible for the merge? And what does the merge owner do when one worker never comes back?

## The short answer

TODO: fill from `results/runs.jsonl` after the benchmark run.

## The experiment

The shared world is Harbour Bikes as real files: [`fixtures/harbour-bikes/`](../fixtures/harbour-bikes/) (eight services with code, dependency files, network config and a `service.yaml` each, plus the hub file `platform.yaml`). Twelve risks are planted in it. The same world and the same checker serve folder 02. This time the workers write:

- **The task:** each worker fixes the risks in its own services, in its own git worktree of a scratch copy of the repo. Platform defaults (HTTP timeout, rate limit, backup policy, minimum instances) live in `platform.yaml`, which every service inherits.
- **The split is fixed and built to collide.** No lead plans it. Three workers get 3, 3 and 2 services (bookings-api, maintenance-scheduler, admin-console / notifications, customer-profiles, fleet-telemetry / pricing-engine, payments-gateway), arranged so that for every shared hub key the services that want it sit on at least two workers. The worker prompt tells workers to fix a shared default at the hub rather than override it per service, and not to hold back for services they cannot see. That nudge is part of the design: without it, workers satisfied their own needs with `service.yaml` overrides and never collided.
- **Traps for regressions.** pricing-engine inherits the hub timeout and needs at most 300 ms; payments-gateway needs at least 20 requests per second per caller. A hub value that is right for the worker who set it can break a service whose worker never saw it.
- **The workers** (Claude Sonnet 5.5) answer in one reply with the full new text of every file they change, plus hub changes as `{key, value, reason}`. They have no tools. They get [`CONTRACT.md`](../fixtures/harbour-bikes/CONTRACT.md) verbatim, their own service files and read-only context (`platform.yaml`, `teams.yaml`, `libs/platform_config.py`). The answer key `CHECKER.md` is never copied into the scratch repo.
- **The harness applies their edits:** it writes the files, applies hub changes to `platform.yaml`, commits in the worker's worktree and merges the branches with real `git merge`. Textual conflicts come from git, per-key conflicts from comparing the hub values the workers set.
- **Partial failure.** In half the runs of each automated arm (rounded toward failed) one worker returns an invalid payload after its retry, injected by the harness, rotating through the workers. In the human arm the middle run fails. Every arm ends with the same merge report, written by Claude Opus 5.5 and told which workers returned nothing. The score is whether the report names the services that were never fixed or reports success silently (a plain text match on the service names; the report is stored in full).
- **Scoring** is the shared checker only ([`fixcheck.check()`](../src/topologies/fixcheck.py), rules in [`CHECKER.md`](../fixtures/harbour-bikes/CHECKER.md)): risks fixed of 12, regressions against the numeric needs, and edits that fall outside the contract (reported separately from "not fixed"). It parses and executes files; there is no folder-local rule and no model judging. The predictions were written before any run: [`PREDICTIONS.md`](../PREDICTIONS.md).

Four arms, who owns the merge:

| Arm | Rule | Runs |
|---|---|---|
| `first-wins` | The first worker commit to touch a key wins; later conflicting values are dropped and logged. | 10 |
| `supervisor-merges` | Claude Opus 5.5 sees each conflict (key, both values, both reasons, which services each worker owns, the hub file) and writes the merged value. It does not see the other services' files. | 10 |
| `hub-owner` | Workers cannot edit `platform.yaml`; they send change requests and Claude Opus 5.5 decides the final value of every key. | 10 |
| `human-decides` | A terminal prompt per conflict: key, both values with reasons, `[a]/[b]/[e]dit value`. The timer starts when the conflict is shown. Choice and seconds are logged, and so is the supervisor's pick for the same conflict, made blind beforehand. | 3 (TODO: 5 if about 3 conflicts per run) |

## Results

TODO: table from `results/summary.md`. Every number in this README must come from `results/runs.jsonl`.

| Arm | Runs | Risks fixed of 12 (mean) | Runs with a regression | Outside contract (mean) | Report names the failed services | Tokens per run (mean) |
|---|---|---|---|---|---|---|
| `first-wins` | TODO | TODO | TODO | TODO | TODO | TODO |
| `supervisor-merges` | TODO | TODO | TODO | TODO | TODO | TODO |
| `hub-owner` | TODO | TODO | TODO | TODO | TODO | TODO |
| `human-decides` | TODO | TODO | TODO | TODO | TODO | TODO |

Human seconds per conflict: TODO.

## Limits

- One task, small n: ten runs per automated arm, three (or five) for the human arm.
- Claude models only: Sonnet 5.5 workers, Opus 5.5 reducer, one backend (`claude-cli`).
- The harness applies the edits. This measures who resolves a collision, not how often real teams collide or how well an agent edits files. Workers reply once with full file contents; tool-using workers that explore, run tests and commit themselves are a different experiment.
- The traps were designed by the authors, and so was the split. Pre-registering the checker and the predictions makes that visible, it does not remove it.
- The merge report is scored by a text match on service names. A mention is not proof that the report says "unfixed"; read the stored reports.
- Commit order in `first-wins` is whichever worker replies first, so it is a race, not a design.
- The human arm is one person at one terminal.
- Token counts include Claude Code's fixed prompt per call (see the [root README](../README.md#setup)); they are counts, not cost.
- Comparisons hold inside this folder only, never against 01 or 02.

## Run it yourself

```bash
uv sync
uv run pytest                                                              # no model calls
uv run 03-fan-out-partial-reducer/run.py --arm supervisor-merges --out /tmp/claude-50629/scratch.jsonl   # one run, scratch file
uv run 03-fan-out-partial-reducer/run.py --arm hub-owner --fail-worker w3 --out /tmp/claude-50629/scratch.jsonl
uv run 03-fan-out-partial-reducer/experiment.py                            # three automated arms, n=10, results/runs.jsonl
uv run 03-fan-out-partial-reducer/experiment.py --human                    # human-decides, 3 runs, you at the terminal
uv run 03-fan-out-partial-reducer/experiment.py --human --human-n 5        # five runs instead of three
uv run 03-fan-out-partial-reducer/experiment.py --summarise-only           # rewrite results/summary.md
```

`experiment.py` refuses to run while `PREDICTIONS.md` contains "DRAFT"; `run.py` is always allowed and refuses to write `results/runs.jsonl`. Keep one setup per `runs.jsonl`: the summary refuses a file that mixes models, served models or backends.

## What's in the code

| File | What it does |
|---|---|
| [`fanout.py`](fanout.py) | The workers, the run loop, the merge report, scoring through the shared checker and the run record. |
| [`fanout_arms.py`](fanout_arms.py) | The four resolution rules: first wins, supervisor, hub owner, human. |
| [`fanout_prompts.py`](fanout_prompts.py) | Every prompt and reply schema. |
| [`fanout_repo.py`](fanout_repo.py) | The scratch git repo, one worktree per worker, real `git merge`. |
| [`fanout_hubfile.py`](fanout_hubfile.py) | Reading and editing `platform.yaml`. |
| [`fanout_config.py`](fanout_config.py) | The fixed split, the arms, the models. |
| [`run.py`](run.py) | One run, printed. |
| [`experiment.py`](experiment.py) | The benchmark loop, the failure schedule, the summary and the pre-registration guard. |
