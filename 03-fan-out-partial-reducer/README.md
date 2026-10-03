# 03 · Fan-out with a partial reducer

## The question

When several workers write to the same shared file at the same time, who is responsible for the merge? And what does the merge owner do when one worker never comes back?

## The short answer

TODO: fill from `results/runs.jsonl` after the benchmark run.

## The experiment

The same eight service cards and twelve planted risks as in [01](../01-orchestrator-worker/), copied into [`fixtures/`](fixtures/). This time the workers write:

- **The task:** each worker fixes the risks on its cards in its own git worktree. A setting that applies across services lives in a shared file, [`platform.yaml`](fixtures/platform.yaml), and the cards refer to it by key.
- **The split is fixed and built to collide.** No lead plans it. Three workers get 3, 3 and 2 cards, arranged so that for every shared key, the cards that want it sit on at least two different workers (timeout, backup policy, dependency pinning, minimum instances). Two workers that set the same key to different values are a conflict.
- **The workers** (Claude Sonnet 5.5) answer in one reply with the full new text of each card they change, plus a one-line reason per hub key they change. They have no tools. **The harness applies their edits:** it writes the files, commits in the worker's worktree and merges the branches with real `git merge`. Textual conflicts come from git, per-key conflicts from comparing the `platform.yaml` values.
- **Two traps for regressions.** pricing-engine's call to demand-model must give up within 300 ms, and the card inherits the hub's timeout. payments-gateway takes 20 requests per second per caller and inherits the hub's rate limit. A hub value that is fine for the worker who set it can break the card of a worker who never saw it.
- **Partial failure.** In half the runs of each automated arm (rounded toward failed) one worker returns an invalid payload after its retry, injected by the harness. The merge owner still writes its report; the score is whether the report names the cards that were never fixed or reports success silently.
- **Scoring** is a deterministic checker with no model judging: one rule per planted risk (fixed or not) and one per planted need (regressed or not). The rules are in [`CHECKER_RULES.md`](CHECKER_RULES.md).

Four arms, who owns the merge:

| Arm | Rule | Runs |
|---|---|---|
| `first-wins` | The first worker commit to touch a key wins; later conflicting values are dropped and logged. | 5 |
| `supervisor-merges` | Claude Opus 5.5 sees each conflict (key, both values, both reasons, all merged cards) and writes the merged value. | 5 |
| `hub-owner` | Workers cannot edit `platform.yaml`; they send change requests and Claude Opus 5.5 decides the final values. | 5 |
| `human-decides` | A terminal prompt per conflict: key, both values with reasons, `[a]/[b]/[e]dit value`. The timer starts when the conflict is shown. Choice and seconds are logged, and so is the supervisor's pick for the same conflict, made blind beforehand. | 3 |

## Results

TODO: table from `results/summary.md`. Every number in this README must come from `results/runs.jsonl`.

## Limits

- One small task, five runs per automated arm and three for the human arm.
- The split is built to collide, and the harness applies worker edits: this measures who resolves a collision, not how often real teams collide or how well a worker edits files.
- Workers reply once with full file contents. Tool-using workers that explore, run tests and commit themselves are a different experiment.
- The checker reads evidence that a fix is stated, not that code runs. Its looseness is listed in `CHECKER_RULES.md`.
- The human arm is one person at one terminal.
- Token counts include Claude Code's fixed prompt per call (see the [root README](../README.md#setup)); they are counts, not cost.
- Comparisons hold inside this folder only, never against 01 or 02.

## Run it yourself

```bash
uv sync
uv run pytest                                                            # no model calls
uv run 03-fan-out-partial-reducer/run.py --arm first-wins                # one run of one arm
uv run 03-fan-out-partial-reducer/run.py --arm hub-owner --fail-worker w3
uv run 03-fan-out-partial-reducer/experiment.py --n 5                    # three automated arms, results/runs.jsonl
uv run 03-fan-out-partial-reducer/experiment.py --human                  # human-decides, 3 runs, you at the terminal
uv run 03-fan-out-partial-reducer/experiment.py --summarise-only         # rewrite results/summary.md
```

Keep one setup per `runs.jsonl`: the summary refuses a file that mixes models, served models or backends.

## What's in the code

| File | What it does |
|---|---|
| [`fanout.py`](fanout.py) | The workers, the four arms, the reducer calls, the merge report and the run record. |
| [`scratch_repo.py`](scratch_repo.py) | The throwaway git repo, one worktree per worker, real `git merge`. |
| [`hubfile.py`](hubfile.py) | Reading and editing `platform.yaml`. |
| [`fixcheck.py`](fixcheck.py) | The deterministic checker. |
| [`experiment.py`](experiment.py), [`run.py`](run.py) | The experiment loop and summary, and a single run. |
