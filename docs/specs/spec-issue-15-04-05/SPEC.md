---
id: SPEC-issue-15-04-05
companions:
  - experiment-design.md
  - predictions-draft.md
  - implementation-plan-04.md
  - implementation-plan-05.md
  - ../spec-issue-14-02-03/SPEC.md
sources: []
---

> **Canonical contract.** This SPEC and the files in `companions:` are the complete contract for what to build, test, and validate. Derived from `.memlog.md`; do not hand-edit.

# Issue 15: hierarchy and event bus, domain boundaries are team boundaries

## Why

Issue 14 (published 6 Oct 2026) closed with: "Next week, one level up. Leads managing other leads, and agents that talk only through a shared message channel. Two questions: should the agent layers match your team layers, and who looks after the shared channel?" Issue 15 ships Tue 13 Oct 2026 and must answer both with a number from its own run. The series reads agent topologies as org charts: 04 tests the Inverse Conway Maneuver for agents (cut the agent tree along team boundaries, or along technology?), 05 tests the platform owning the shared channel as a product (four-rights: platform enforces the bus and its trace context).

## Capabilities

- **CAP-1**
  - **intent:** A three-tier hierarchy (8 single-service Sonnet workers, Opus sub-leads, an Opus platform lead that owns `platform.yaml`) fixes Harbour Bikes under the 03 platform mandate, in four arms: `flat`, `by-team`, `by-runtime`, `by-runtime-registry`.
  - **success:** One run index produces four arm rows from one shared set of worker replies; each row holds the requests every tier sent, the platform lead's decisions, and `fixcheck.check()` on that arm's repo.
- **CAP-2**
  - **intent:** 04 traces each cross-boundary coupling to the tier where it was resolved.
  - **success:** Every hub request carries a deterministic harm list (regression needs it would break with no override) marked inside or outside the requester's subtree; each regression need gets one ladder outcome per row (not-threatened, absorbed-at-sub-lead, caught-at-top, reached-hub, introduced-by-top).
- **CAP-3**
  - **intent:** The 04 benchmark turns runs into a per-arm result the issue can quote.
  - **success:** `experiment.py --n 10` writes `results/runs.jsonl` and `results/summary.md` with the metrics in `experiment-design.md`; the README states question, design choices, result, predictions scored, limits.
- **CAP-4**
  - **intent:** An in-process event bus delivers the scanner's `risk.reported` messages to six team consumers (Sonnet) under three ownership arms: `no-owner`, `steward-agent`, `platform-owned`.
  - **success:** One run per arm completes with the same consumers and prompts; every publish, delivery, outcome, exception and corrective action is in the run record; the repo is scored by `fixcheck.check()`.
- **CAP-5**
  - **intent:** Faults are injected deterministically on the bus.
  - **success:** Run *i* injects the same four faults (malformed, wrong-service, consumer-crash, duplicate) on the same four distinct routable risks in every arm; fleet-telemetry's two messages are unroutable in every run by the fixture's own `owner: null`.
- **CAP-6**
  - **intent:** 05 measures whether a failure can be traced to its origin.
  - **success:** The harness computes a ground-truth cause per unfixed risk; one investigator prompt, identical across arms, reads each arm's records and is scored on cause accuracy and false assurance; platform-owned runs also score a paired investigator with trace context stripped.
- **CAP-7**
  - **intent:** The 05 benchmark turns runs into a per-arm result the issue can quote.
  - **success:** `experiment.py --n 10` writes `results/runs.jsonl` and `results/summary.md` with the metrics in `experiment-design.md`; README as in CAP-3.
- **CAP-8**
  - **intent:** Both benchmarks are pre-registered.
  - **success:** `predictions-draft.md` exists; each `experiment.py` refuses while it is missing or says DRAFT, or while `PREDICTIONS.md` lacks the folder's section; dry runs are disclosed in the frozen predictions.
- **CAP-9**
  - **intent:** The shared world and answer key are reused, not changed.
  - **success:** `src/topologies/fixcheck.py`, `harbour.py`, `fixtures/harbour-bikes/` (including CHECKER.md and CONTRACT.md) are byte-identical to 7888d48; the 305 existing tests still pass. *Amended 2026-10-12, after both benchmarks:* `fixcheck.py` changed one label (an untouched `owner: null` is not fixed, not outside contract; CHECKER.md entry of that date), proven by replay to leave risks fixed and regressions unchanged; CONTRACT.md and the fixture stay byte-identical.

## Constraints

- Models: Sonnet 5.5 (`claude-sonnet-5-5`) for workers and consumers; Opus 5.5 (`claude-opus-5-5`) for sub-leads, platform lead, steward, platform agent, investigator. Backend `claude-cli`. Every call through `src/topologies/model.py`; schema-checked calls through `topologies.guards.call_and_validate`.
- Arms: 4 in 04, 3 in 05; n=10 per arm. Comparisons hold only inside a folder.
- Scoring: `fixcheck.check()` for fixed and regressed; every other metric is computed by code from run records. No model judges, no regex over prose. The investigator is a measured subject, scored against harness ground truth, never a judge.
- Every agent that decides something gets the rules it decides under (CONTRACT.md, the mandate line, the message schema), so no tier is judged against a contract it never saw.
- The two folders share no new code and no edited file; the coordinator alone edits `pyproject.toml`, root `README.md` and `PREDICTIONS.md`.
- Runs persist every prompt-relevant input summary and every reply, parse with `raw_decode` first, rotate arm order per index, and refuse to summarise mixed models, served models or backends.
- Plain Python, `uv`, the existing thin client; no agent framework, no tools-on calls; the harness writes files.
- No client names, client code or client material.
- Schedule: both folders built in parallel Mon 12 Oct; fallback to 04 only if 05 is not green by 15:00.

## Non-goals

- Changing fixcheck, CHECKER.md, CONTRACT.md or the fixture.
- Failure injection in 04, hub contention in 05.
- A lead that plans the split (01 measured it), a model-planned cut, or tool-using agents.
- A point-to-point arm in 05 (03 is the no-bus fan-out).
- Cross-folder or cross-model comparisons.

## Success signal

- Before Tue 13 Oct 2026, `uv run pytest` passes with no model calls, both folder READMEs carry measured tables from their own `results/runs.jsonl`, and each prediction is scored hit or miss.

## Assumptions

- Sub-leads in `by-runtime` forward a hub timeout raise often enough for the coupling to be tested (03 workers asked for 12-15 s and 5 s in every mandated run). Checked in dry runs; if false, the benchmark still runs unchanged and the prediction says so.
- One corrective round in 05 is enough to show the difference between owners; a second round would raise cost without a new question.

## Open Questions

- Who approves the Issue 15 predictions: Thiago, or a fresh delegation logged in `.memlog.md`.
