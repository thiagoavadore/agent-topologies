# 03 · Fan-out with a partial reducer

## The question

When several workers write to the same shared file at the same time, who is responsible for the merge? And what does the merge owner do when one worker never comes back?

## The short answer

Who owns the merge decided who broke. Under the platform mandate, the shared timeout was contested in 6 of 10 runs per arm. With `first-wins`, pricing-engine regressed in 10 of 10 runs. With `supervisor-merges`, 4 of 10. With `hub-owner`, 0 of 10.

Where a local route was open, there was nothing to merge. With overrides, or with timeouts in code, workers never contested the shared timeout (0 of 10 runs in each baseline). The platform forked instead: about 3 distinct effective timeouts across the 8 services (3.0 and 2.9 on average).

The `supervisor-merges` regressions have one cause. All 4 came from runs with no conflict, so the supervisor was never consulted, and an uncontested raise of the hub timeout broke pricing-engine, a service no worker owned. `hub-owner` sees every change. It kept the hub default at 300 ms in 9 of 10 runs and gave bookings-api and notifications their own overrides.

## The experiment

The shared world is Harbour Bikes as real files: [`fixtures/harbour-bikes/`](../fixtures/harbour-bikes/) (eight services with code, dependency files, network config and a `service.yaml` each, plus the hub file `platform.yaml`). Eleven risks are planted in it (a twelfth was removed before the benchmark, see below). The same world and the same checker serve folder 02. This time the workers write:

- **The task:** each worker fixes the risks in its own services, in its own git worktree of a scratch copy of the repo. Platform defaults (HTTP timeout, rate limit, backup policy, minimum instances) live in `platform.yaml`, which every service inherits.
- **The split is fixed and built to collide.** No lead plans it. Three workers get 3, 3 and 2 services (bookings-api, maintenance-scheduler, admin-console / notifications, customer-profiles, fleet-telemetry / pricing-engine, payments-gateway), arranged so that for every shared hub key the services that want it sit on at least two workers.
- **A ladder of routes around the hub.** A worker that cannot see other services meets its own need without touching the hub if it can: with a `service.yaml` override, or with a literal timeout in code. In the first dry runs workers did exactly that and nobody touched the hub, so there was nothing to merge. Rather than word collisions into existence, the experiment measures each route as an arm and says so:
  1. `overrides-allowed`: workers may write overrides and code literals.
  2. `code-local`: no overrides, code literals allowed.
  3. Platform-mandated (`first-wins`, `supervisor-merges`, `hub-owner`, `human-decides`): no overrides, and the timeout in `charge()` or `send_email()` must be `http_timeout("<service>")`, which reads the hub. The harness enforces this like CI: after a worker replies, its Python files are checked with the shared AST helpers, a file whose timeout is anything else is reverted (its other files are kept) and the rejection is logged per worker. The harness also strips any override a worker writes. Only the merge owner may grant an override.
  The worker prompt states each rule in one neutral line, identical across the mandated arms. The shared contract is untouched; the rules are in the prompt. The finding is whether contention is a property of the architecture (is the hub the only seam?) rather than of the agents.
- **Traps for regressions.** pricing-engine inherits the hub timeout and needs at most 300 ms; payments-gateway needs at least 20 requests per second per caller. A hub value that is right for the worker who set it can break a service whose worker never saw it.
- **The workers** (Claude Sonnet 5.5) answer in one reply with the full new text of every file they change, plus hub changes as `{key, value, reason}`. They have no tools. They get [`CONTRACT.md`](../fixtures/harbour-bikes/CONTRACT.md) verbatim, their own service files and read-only context (`platform.yaml`, `teams.yaml`, `libs/platform_config.py`). The answer key `CHECKER.md` is never copied into the scratch repo.
- **The harness applies their edits:** it writes the files, applies hub changes to `platform.yaml`, commits in the worker's worktree and merges the branches with real `git merge`. Textual conflicts come from git, per-key conflicts from comparing the hub values the workers set.
- **Partial failure.** In half the runs of each automated arm (rounded toward failed) one worker returns an invalid payload after its retry, injected by the harness, rotating through the workers. In the human arm the middle run fails. Every arm ends with the same merge report, written by Claude Opus 5.5 and told which workers returned nothing. The score is whether the report names the services that were never fixed or reports success silently (a plain text match on the service names; the report is stored in full).
- **Scoring** is the shared checker only ([`fixcheck.check()`](../src/topologies/fixcheck.py), rules in [`CHECKER.md`](../fixtures/harbour-bikes/CHECKER.md)): risks fixed of 11, regressions against the numeric needs, and edits that fall outside the contract (reported separately from "not fixed"). It parses and executes files; there is no folder-local rule and no model judging. The predictions were written before any run: [`PREDICTIONS.md`](../PREDICTIONS.md).

Six arms. Four are platform-mandated and differ in who owns the merge; two are baselines:

| Arm | Rule | Runs |
|---|---|---|
| `first-wins` | The first worker commit to touch a key wins; later conflicting values are dropped and logged. Nobody can grant an override. | 10 |
| `supervisor-merges` | Claude Opus 5.5 sees each conflict (key, values, reasons, which services each worker owns, the hub file, and the stated needs of every service, including services no worker touched) and writes the merged value. It may also grant one service an override. | 10 |
| `hub-owner` | Workers cannot edit `platform.yaml`; they send change requests and Claude Opus 5.5 decides the final value of every key, with the same needs list, and may grant overrides. | 10 |
| `overrides-allowed` | Baseline 1: workers may write their own overrides and code literals, hub conflicts are first-wins. Measures how far workers fork the platform instead of colliding: overrides written per run and distinct effective timeouts across the eight services. | 10 |
| `code-local` | Baseline 2: no overrides, code literals allowed, hub conflicts are first-wins. Measures the second route around the hub, with the same effective and distinct timeouts. | 10 |
| `human-decides` | A terminal prompt per conflict: key, both values with reasons, `[a]/[b]/[e]dit value`. The timer starts when the conflict is shown. The same needs list is shown, and `[o]verride` grants one service its own value for an HTTP key before the final choice. Choice and seconds are logged, and so is the supervisor's pick (and its grants) for the same conflict, made blind beforehand. | 3 |

## Results

Run on 5 and 6 Oct 2026. Tokens count everything sent and received across the whole run. The automated arms ran 10 times each, the human arm 3.

| Arm | Runs | Timeout contested | Risks fixed of 11 (mean) | Runs with a regression | Distinct effective timeouts (mean, of 8 services) | Overrides granted by the merge owner (mean) | Report names the failed services | Tokens per run (mean) |
|---|---|---|---|---|---|---|---|---|
| `first-wins` | 10 | 6 of 10 | 8.7 | 10 of 10 | 1.4 | 0.0 | 5 of 5 | 21,257 |
| `supervisor-merges` | 10 | 6 of 10 | 8.6 | 4 of 10 | 2.0 | 1.1 | 5 of 5 | 22,906 |
| `hub-owner` | 10 | 6 of 10 | 8.4 | 0 of 10 | 2.5 | 1.3 | 5 of 5 | 25,652 |
| `overrides-allowed` | 10 | 0 of 10 | 9.0 | 0 of 10 | 3.0 | 0.0 | 5 of 5 | 17,501 |
| `code-local` | 10 | 0 of 10 | 8.2 | 1 of 10 | 2.9 | 0.0 | 5 of 5 | 21,783 |
| `human-decides` | 3 | 2 of 3 | 9.7 | 3 of 3 | 1.3 | 0.0 | 1 of 1 | 23,982 |

Every regression in every arm is the same one: `pricing-engine.demand_model_timeout`. "Report names the failed services" counts the runs where a worker was made to fail, and the report named every service that worker owned.

What this tells us:

- **Contention comes from the architecture.** With a local route open, workers met their own needs without touching the hub, so no key was contested. Close the routes and the same workers collide on `http.default_timeout` in 6 of 10 runs.
- **Without an owner, the first value wins and pricing-engine pays.** `first-wins` ended with a hub timeout above 300 ms in every run (3s to 15s), and nobody could grant pricing-engine an exception. 10 of 10 regressed.
- **A supervisor only helps when it is asked.** Of the 10 `supervisor-merges` runs, 6 had a conflict and 4 had none. The 6 had no regression. The 4 without a conflict all regressed: one worker raised the timeout alone, the supervisor was never consulted, and the unreviewed value broke pricing-engine. In the six conflict runs it granted pricing-engine an override in each case (6 of 6).
- **The owner that sees every change did best.** `hub-owner` reviews every request, contested or not. It had 0 regressions in 10. Its usual move was to keep the default at 300 ms and grant overrides: bookings-api and notifications each got one in 5 of 10 runs, 13 grants over 10 runs. That is also why it ends with more distinct timeouts (2.5) than the other mandated arms.
- **Risks fixed barely separate the arms.** The means run from 8.2 to 9.0 with minimums between 3 and 6, at n=10. Do not rank the arms on it.
- **The merge report did not separate the arms.** In half the runs one worker fails on purpose. In every run where a worker returned nothing, in every arm, the report named that worker's services. The report is told who failed, so naming them was easy. Whether the merge itself left risks open is not measured by this column.

### Human arm (anecdote)

One person (Thiago), 3 runs. This is a disclosed anecdote, not a measurement. The result: a regression in 3 of 3 runs, 0 overrides granted.

| Run | What happened |
|---|---|
| 1 | Chose 15s for `http.default_timeout` (the supervisor chose the same). pricing-engine broke. Rate limit set to 30/s (the supervisor chose 50/s). |
| 2 | No conflict: worker 1 had failed. An uncontested hub raise broke pricing-engine, the same path as the supervisor's 4 regressions. |
| 3 | Chose 5s (the supervisor chose 15s). pricing-engine broke, bookings-api went unfixed. Rate limit set to 30/s (the supervisor agreed). |

The runs are contaminated. Run 1's timing includes a chat with an assistant. The assistant explained the rate-limit conflict during run 1 and the timeout trap after run 1, before runs 2 and 3. So the human seconds (mean 126.7 per conflict) are not quotable, and runs 2 and 3 were not made blind. Every decision is in [`results/summary.md`](results/summary.md).

Raw data: [`results/summary.md`](results/summary.md) for the full tables (contested keys, overrides, human decisions), [`results/runs.jsonl`](results/runs.jsonl) for every run (the merge report, every resolution, the merged diff).

## Two things in the data to know about

**A label artifact.** Every automated arm shows 0.2 "outside contract" on average. That is a mislabel. In runs where worker 2 is the one made to fail (runs 3 and 9), nobody touches the fleet-telemetry owner, the planted risk `fleet-telemetry.no_owner`, and the checker labels it "outside contract" instead of "not fixed". Risks-fixed counts are unaffected, and no worker edit broke the contract. It was left alone because the checker was frozen before the benchmark. It is debt.

**The answer key changed before the benchmark.** The first design had twelve planted risks. `maintenance-scheduler.single_point_of_failure` was removed: in 5 of 5 dry runs the worker declined to raise `instances`, with the right reason (its SQLite files live on a VM-local disk, so a second instance splits state) and the contract offered no legal fix. A risk that cannot be fixed inside the contract measures the answer key, not the workers. See [`CHECKER.md`](../fixtures/harbour-bikes/CHECKER.md#answer-key-changes-before-the-benchmark). Same lesson as folder 01: whoever writes the answer key decides the result.

## Predictions

Written before any run and frozen on 5 Oct 2026 in [`PREDICTIONS.md`](../PREDICTIONS.md). Results that disagree are reported as misses. Where the answer is "about right", the numbers are given.

| Prediction | Result |
|---|---|
| Hub conflicts: `overrides-allowed` about zero | **Hit.** 0 contested keys. |
| Hub conflicts: `code-local` low, rate limit only | **Hit.** 0.2 contested keys: rate limit in 2 of 10 runs, timeout in 0. |
| Hub conflicts: mandated `first-wins` 1 to 2, timeout in most runs | **Hit on the pattern.** Timeout in 6 of 10, rate limit in 3 of 10. The mean is 0.9, just under the range, because 2 runs had none. |
| Hub conflicts: `supervisor-merges` and `human-decides` like `first-wins`; `hub-owner` zero textual | **Hit.** 1.0 and 1.3 contested keys; `hub-owner` 0.0 textual, 1.0 contested. |
| Distinct timeouts: `overrides-allowed` 3 or more, `code-local` 2 or more | **Hit.** 3.0 and 2.9. |
| Distinct timeouts: `first-wins` 1 to 2, `supervisor-merges` 2 | **Hit.** 1.4 and 2.0. |
| Distinct timeouts: `hub-owner` 2 | **Miss.** 2.5: it granted two services their own override in half the runs. |
| Distinct timeouts: `human-decides` 2 if the human grants an override, else 1 | **Hit.** No overrides granted, 1.3. |
| Regressions: `overrides-allowed` low | **Hit.** 0 of 10. |
| Regressions: `code-local` some | **Hit.** 1 of 10. |
| Regressions: `first-wins` highest, nearly every run | **Hit.** 10 of 10. |
| Regressions: `supervisor-merges` near zero | **Miss.** 4 of 10, explained by the uncontested-change finding above. |
| Regressions: `hub-owner` near zero | **Hit.** 0 of 10. |
| Regressions: `human-decides` low | **Miss.** 3 of 3, with the contamination caveat above. |
| Risks fixed: owned merges (`supervisor-merges`, `hub-owner`) highest, `first-wins` lower | **Miss.** 8.6 and 8.4 against 8.7 for `first-wins`. |
| Risks fixed: `overrides-allowed` and `code-local` high | **Mixed.** 9.0 is the highest automated mean; 8.2 for `code-local` is the lowest, with one run at 3. |
| Risks fixed: `human-decides` about equal to `supervisor-merges` | **Loose hit.** 9.7 against 8.6, n=3. |
| Mandate rejections rare | **Hit.** Mean 0.0 in every arm. |
| Failed workers' services named in the report in nearly every run | **Hit.** Every run with a failed worker, every arm. The second half of the prediction, that arms differ on risks the merge left open, is not measured by this column. |
| Tokens: `overrides-allowed` lowest, `hub-owner` highest, `supervisor-merges` above `first-wins` | **Hit.** 17,501, 25,652, and 22,906 against 21,257. |
| Human: tens of seconds per conflict, agrees with the supervisor in most but not all | **Miss.** Mean 126.7 seconds (contaminated). The human matched the supervisor's value in 2 of 4 conflicts. |

The surprises we named did not happen. `first-wins` showed far more regressions than `supervisor-merges` (10 of 10 against 4 of 10), so the owner of the merge did matter. `overrides-allowed` had no conflicts, so contention does come from the architecture.

## Limits

- One task, small n: ten runs per automated arm, three for the human arm.
- Claude models only: Sonnet 5.5 workers, Opus 5.5 reducer, one backend (`claude-cli`).
- The harness applies the edits. This measures who resolves a collision, not how often real teams collide or how well an agent edits files. Workers reply once with full file contents; tool-using workers that explore, run tests and commit themselves are a different experiment.
- The traps were designed by the authors, and so was the split. Pre-registering the checker and the predictions makes that visible, it does not remove it.
- The merge report is scored by a text match on service names. A mention is not proof that the report says "unfixed"; read the stored reports.
- Only a conflicted key reaches the supervisor and the human; a hub value only one worker proposes is applied unreviewed in those arms (`hub-owner` reviews every request). A granted override lives in `service.yaml`, rewritten by the harness (comments in that file are lost).
- The mandate and the override rule are design choices made after dry runs showed no collisions without them; the dry runs are disclosed in `PREDICTIONS.md`. In the mandated arms contention is imposed by architecture, so those rows say what happens once the hub is the only seam, not how often it is.
- A harness-side mandate check is strict: any form other than `http_timeout("<service>")` reverts the file, including other edits in that same file.
- Commit order in `first-wins` is whichever worker replies first, so it is a race, not a design.
- The human arm is one person at one terminal.
- Token counts include Claude Code's fixed prompt per call (see the [root README](../README.md#setup)); they are counts, not cost.
- Comparisons hold inside this folder only, never against 01 or 02.

## Run it yourself

```bash
uv sync
uv run pytest                                                              # no model calls
uv run 03-fan-out-partial-reducer/run.py --arm supervisor-merges --out /tmp/claude-50629/scratch.jsonl   # one run, scratch file
uv run 03-fan-out-partial-reducer/run.py --arm code-local --out /tmp/claude-50629/scratch.jsonl
uv run 03-fan-out-partial-reducer/run.py --arm hub-owner --fail-worker w3 --out /tmp/claude-50629/scratch.jsonl
uv run 03-fan-out-partial-reducer/experiment.py                            # five automated arms, n=10, results/runs.jsonl
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
| [`fanout_mandate.py`](fanout_mandate.py) | The mandate check on a worker's Python files. |
| [`fanout_hubfile.py`](fanout_hubfile.py) | Reading and editing `platform.yaml`. |
| [`fanout_overrides.py`](fanout_overrides.py) | Finding, stripping and granting `service.yaml` overrides. |
| [`fanout_config.py`](fanout_config.py) | The fixed split, the arms, the models. |
| [`run.py`](run.py) | One run, printed. |
| [`experiment.py`](experiment.py) | The benchmark loop, the failure schedule, the summary and the pre-registration guard. |
