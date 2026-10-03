# 02 · Pipeline with checkpoints

## The question

In a pipeline, each agent only sees what the one before it handed over. When stage 3 fails, whose problem is it? Where does the failure actually start, and how much of the pipeline has to run before anyone notices?

## The short answer

TODO: written from `results/summary.md` after the benchmark run.

## The experiment

Four stages review the same eight service cards and twelve planted risks as [folder 01](../01-orchestrator-worker/) (copied into [`fixtures/`](fixtures/), 01's files are untouched):

1. **Extract:** facts per card, each with a quoted line.
2. **Classify:** each risky fact becomes a risk with one of eight categories.
3. **Prioritise:** every risk ranked once.
4. **Remediation plan:** one action per ranked risk.

All stages run on Claude Sonnet 5.5. Each sees only the previous stage's output (stage 1 sees the cards). There is no orchestrator.

A **checkpoint** has two parts: a contract check (valid JSON, every card accounted for), then a Sonnet 5.5 gate that gets the stage's input and output and answers pass or fail with a reason. A failed checkpoint **stops the pipeline** and records the stage whose output it judged. There is no re-run loop, so "caught at stage N" means "stopped there".

Three arms:

- **none:** no checkpoints.
- **end-only:** one checkpoint after stage 4.
- **every-handoff:** a checkpoint after each of stages 1 to 4.

**Fault injection.** The harness damages the stage-1 output before anyone downstream (or any gate) sees it. Two kinds, alternating: a planted risk's fact moved to the wrong card (the next card by name), or that fact dropped. Which risk is attacked rotates over five planted risks. The fault of run *i* is the same in every arm, and the arm order rotates per run.

Five runs per arm: runs 1, 3 and 5 are faulted, runs 2 and 4 are clean.

**Metrics.** Per injected fault: `caught-stage-N`, or `reached-production` if no checkpoint stopped it and the final plan has no action for the attacked card and category. Two other outcomes are reported, not hidden: `absorbed` (not caught, but the plan is fine anyway) and `not-injected` (no stage-1 fact matched the attacked risk, so nothing was damaged). Per arm: tokens (total and the gate share), false rejections (clean runs a checkpoint stopped), and recall against the answer key on the final plan, over runs that reached one.

## Run it

```bash
uv sync
uv run pytest                                              # no model calls

cd 02-pipeline-checkpoint
uv run python run.py --arm every-handoff --fault wrong-card   # one run, with a trace
uv run python experiment.py --n 5                             # the benchmark: 3 arms x 5 runs, at most 85 model calls
uv run python experiment.py --summarise-only                  # rebuild results/summary.md
```

`experiment.py` appends to `results/runs.jsonl` and refuses to summarise a file that mixes models or backends. Move old runs aside before changing either. Every stage output, gate verdict with its reason, served model, token count and wall time is in the JSONL.

## Results

TODO: table from `results/summary.md`.

## Limits

- Five runs per arm, one task, one fault site (stage 1). Not a general claim about pipelines.
- The gate sees only one stage's input and output. After stage 1 it never sees the cards, so `end-only` is judged on what a late gate can know, not on what a better-informed one could.
- A gate "catch" is credited to the stage whose output it judged, which for `end-only` is stage 4 even though the fault started at stage 1. That is the finding, not a bug.
- The fault is injected only if a stage-1 fact on the target card quotes a known phrase for the risk (see `injection.py`). Runs where nothing matched are marked `not-injected` and left out of the catch counts.
- A dropped fact that is the only fact for its card is caught by the contract check, not by the model gate.
- A stage that stays invalid after one retry, or a gate that cannot run, ends the run with no verdict; those runs are listed in the summary.
- Token counts include Claude Code's per-call overhead (see the [root README](../README.md#setup)); `every-handoff` makes the most calls.
