# 02 · Pipeline with checkpoints

## The question

In a pipeline, each agent only sees what the one before it handed over. When stage 3 fails, whose problem is it? Where does the failure actually start, and how much of the pipeline runs before anyone notices?

## The short answer

TODO: written from `results/summary.md` after the benchmark run.

## The experiment

Four stages review the real files of the Harbour Bikes world (eight services, one shared hub, 11 planted risks; see [`fixtures/harbour-bikes/`](../fixtures/harbour-bikes/) and the catalogue in `src/topologies/harbour.py`). Nothing in this folder writes to those files.

1. **Extract:** facts, each keyed by service, file and locator (a function and call in code, a dotted key path in YAML or JSON).
2. **Classify:** each risky fact becomes a risk with one of eight categories.
3. **Prioritise:** every risk ranked once.
4. **Remediation plan:** one action per ranked risk.

All stages run on Claude Sonnet 5.5 via `claude -p`. Each sees only the previous stage's output (stage 1 sees the files). There is no orchestrator.

A **checkpoint** has two parts: a contract check (valid JSON, every service accounted for), then a Sonnet 5.5 gate that gets the stage's input and output and answers pass or fail with a reason. A failed checkpoint **stops the pipeline** and records the stage whose checkpoint failed, the stage that gets the blame. There is no re-run, so "caught at stage N" means "stopped there".

Three arms:

- **none:** no checkpoints.
- **end-only:** one checkpoint, after stage 4.
- **every-handoff:** a checkpoint after each of stages 1 to 4.

**Fault injection.** The harness damages the stage-1 output before the contract check, the gate or any later stage sees it. It targets a catalogue risk by ID: it finds the stage-1 facts citing the risk's file and locator (all facts citing the file if none cite the locator; the run records which), then either **moves** them to the wrong service (the next service in catalogue order) or **drops** them. Stage 1 must cite every file (one retry, else the run ends without a verdict), so an injection always lands. The kind alternates and the target rotates over five risks. The fault of run *i* is the same in every arm, and the arm order rotates per run.

Ten runs per arm: even runs are faulted (5), odd runs are clean (5).

**Metrics.**

- Per injected fault: `caught-stage-N`, or `reached-production` when no checkpoint stopped it and the stage-4 plan has no grounded finding for the attacked risk. `absorbed` (not caught, but the plan still has the finding) and `no-verdict` (the run ended on a failed stage or an unreadable gate) are reported, not hidden. There is no `not-injected` outcome; the code asserts it cannot happen.
- Per arm: tokens (total and the gate share), and the checkpoint rejections of clean runs, split into false and justified (see "The checkpoint was wrong the first time").
- **Evidence-grounded recall:** a plan item counts only if it names the right risk ID (service and category) and cites the risk's file and locator. Measured only on runs that reach stage 4, with the number of such runs shown beside it. A run a checkpoint stops has no plan, so an arm that stops more runs is scored on fewer.

Predictions were written before any run: [`PREDICTIONS.md`](../PREDICTIONS.md). The benchmark refuses to start while that file says `DRAFT`.

## The checkpoint was wrong the first time

*2026-10-05.* In the first benchmark, `every-handoff` stopped **5 of 5 clean runs**. Four were stopped at the stage-2 gate, one at the stage-1 gate. The stage-2 reasons demanded that facts outside the eight categories (a shared admin password with no SSO, a missing alert on a delete-all job) be classified. Stage 2 is not allowed to classify those: it only has eight categories. The gate had been told "nothing that bears on operational risk dropped" and had never been told the stage's contract, so it judged stage 2 against a bar the stage could not meet.

Run on the same rows with the rule below, the five clean rejections split into 4 false and 1 justified (run 4: stage 2 really lost a catalogue risk it had been given). Of the five faulted `every-handoff` runs, four were stopped at stage 1 for the injected fault, and those reasons name the right misattributed or missing fact. The fifth (run 7, a dropped `bookings-api.missing_timeout`) was stopped at stage 2 for a risk stage 2 had not lost, so that fault was never seen and the stop was false. The other arms, with no gate before stage 4, stopped nothing: `none` and `end-only` had 0 of 5 clean rejections.

What changed:

- Each gate now gets the judged stage's own contract: the stage's output schema, the category list (stages 2 to 4), and what the stage may drop by design (stage 2: facts that fit no category; stages 3 and 4: nothing). All four gates use the same wording pattern, in `gate_system()`.
- Rejections are split without a model. A rejection is **justified** when the judged stage's output lacks a catalogue risk whose fact is in its input (for stage 1, the files hold every risk), and **false** otherwise. On a faulted run, a rejection whose lost risks include the injected target counts as the injected fault. The summary reports `false rejections` and `justified rejections` for clean runs, and the justified / false split for other rejections on faulted runs.

The first runs are kept in [`results/2026-10-05-v1-gate-contract-mismatch.jsonl`](results/2026-10-05-v1-gate-contract-mismatch.jsonl) (summary beside it). The lesson, same as in folder 01: whoever writes the checker decides the result, and that includes the gate.

## Run it

```bash
uv sync
uv run pytest                                                  # no model calls

cd 02-pipeline-checkpoint
uv run python run.py --arm every-handoff --fault wrong-service --target bookings-api.missing_timeout --out /tmp/claude-50629/scratch.jsonl
uv run python experiment.py --n 10                             # the benchmark: 3 arms x 10 runs, up to 150 model calls
uv run python experiment.py --summarise-only                   # rebuild results/summary.md
```

`experiment.py` appends to `results/runs.jsonl` and refuses to summarise a file that mixes models or backends. Move old runs aside before changing either. Every stage output, gate verdict with its reason, served models, token count and wall time is in the JSONL.

## Results

TODO: table from `results/summary.md`.

## Limits

- One task, ten runs per arm, one fault site (stage 1), two fault types. Not a general claim about pipelines.
- Claude models only (Sonnet 5.5 in every role, gates included), and token counts include Claude Code's per-call overhead.
- The harness injects the faults; real stage-1 errors look different.
- The traps were designed by the authors; pre-registering the predictions makes that visible, it does not remove it.
- The gate sees one stage's input and output. After stage 1 it never sees the files, so `end-only` is judged on what a late gate can know, not on what a better-informed one could.
- A catch is credited to the stage whose checkpoint failed, which for `end-only` is stage 4 even though the fault started at stage 1. That is the finding, not a bug.
- A dropped fact that was a service's only fact is caught by the contract check, not by the model gate.
- A stage that stays invalid after one retry, or a gate that cannot run, ends the run with no verdict; those runs are listed in the summary.
