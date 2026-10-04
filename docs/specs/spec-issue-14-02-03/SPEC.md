---
id: SPEC-issue-14-02-03
companions:
  - experiment-design.md
  - ../../../01-orchestrator-worker/README.md
sources: []
---

> **Canonical contract.** This SPEC and the files in `companions:` are the complete, preservation-validated contract for what to build, test, and validate. Derived from `.memlog.md`; do not hand-edit.

# Issue 14: pipeline with checkpoints, fan-out with a partial reducer

## Why

A promise to meet and a question to answer. Issue 13 of The Recovering CTO (published 29 Sep 2026) told readers that Issue 14 (Tue 6 Oct 2026) covers handoffs versus parallel streams, folders 02 and 03, "and this time the workers get write access, pointed at the same hub file." The series reads agent topologies as org charts, so each folder must answer an ownership question with a number from its own run: in a pipeline, whose problem is a failure that started at stage 1; in a fan-out, who owns the merge when writers collide on a shared file.

## Capabilities

- **CAP-1**
  - **intent:** A four-stage pipeline (extract, classify, prioritise, remediation plan) reviews the Harbour Bikes cards under three checkpoint arms, with faults injected into the stage-1 output.
  - **success:** One run per arm completes; stage-1 facts are keyed by service and field so every injected fault hits its target ID; each run records, per fault, the stage that caught it or `reached-production`, and every gate failure names the owning stage.
- **CAP-2**
  - **intent:** The 02 experiment turns runs into a per-arm result the issue can quote.
  - **success:** `experiment.py --n 5` writes `results/runs.jsonl` and `results/summary.md` with, per arm, catch stage distribution, tokens, false rejections on clean runs, and evidence-grounded recall (a finding counts only if it cites the field or line); the folder README states the question, run command, result and limits.
- **CAP-3**
  - **intent:** Writer workers fix the risks on their assigned cards, each in its own git worktree, and contend on a shared `platform.yaml` by design.
  - **success:** In a clean run every worker produces a commit in its own worktree, and at least two workers change the same `platform.yaml` key.
- **CAP-4**
  - **intent:** Hub conflicts are resolved under four rules: first wins, supervisor merges, hub owner, named human decides.
  - **success:** Each arm produces a merged repo; the human arm prompts per conflict and logs choice and seconds, with the supervisor's pick for the same conflict logged beside it.
- **CAP-5**
  - **intent:** The 03 score uses the shared checker (CAP-9) to decide which planted risks the merged repo fixed and which service needs a hub change broke.
  - **success:** Every 03 run records risks fixed (of 12) and regressions from the shared checker only; no folder-local fix rules exist.
- **CAP-6**
  - **intent:** The reducer works with partial input when a worker fails.
  - **success:** In failure-injected runs the score records whether the merged result names the unfixed cards or reports success silently.
- **CAP-7**
  - **intent:** Shared code gains what 02 and 03 need without changing what 01 measured.
  - **success:** `Reply` carries `served_model` from the backend response; `src/topologies/guards.py` provides schema-with-retry, token budget and named-skip helpers used by 02 and 03; 01's tests still pass and its files are unchanged.
- **CAP-9**
  - **intent:** One shared real-file Harbour Bikes world (same 8 services, same 12 risks) with stable risk IDs and a checker that decides fixes by parsing and executing files, never by matching prose.
  - **success:** The checker accepts a reference fixed repo (12 of 12, 0 regressions), rejects every case in the fake-fix suite, flags the unchanged repo as 0 fixed, and its tests run with no model calls.
- **CAP-10**
  - **intent:** The benchmark is pre-registered so the answer key cannot be tuned after seeing results.
  - **success:** `PREDICTIONS.md`, the checker and the fake-fix suite are committed, and Thiago's approval is logged in `.memlog.md`, before the first benchmark run; any later checker change carries a dated note and a full re-run.
- **CAP-8**
  - **intent:** The 03 experiment turns runs into a per-arm result the issue can quote.
  - **success:** `experiment.py` writes `results/runs.jsonl` and `results/summary.md` with the metrics in `experiment-design.md`; the README says the split is built to collide and that the harness applies worker edits.

## Constraints

- Models: Opus 5.5 (`claude-opus-5-5`) for supervisor/reducer roles, Sonnet 5.5 (`claude-sonnet-5-5`) for executors and gates, backend `claude-cli`. Comparisons hold only within a folder; never against 01.
- Every number in a README or the issue comes from that folder's own run, never a catalogue threshold.
- No rule in any checker matches prose; facts a check needs live in parseable files (code, dependency files, network config, `service.yaml`, `platform.yaml`).
- The fixture world and answer key have exactly one home, shared by 02 and 03.
- Runs: n=10 per automated arm; human arm n=3 (5 if a confirmation run shows about 3 conflicts per run).
- Each README states the irreducible limits: one task, small n, Claude models only, the harness applies edits, the traps were designed by the authors.
- Plain Python, `uv`, the existing thin client; no agent framework, no tools-on calls. Workers return full file contents; the harness writes and commits.
- 01's files and published results stay untouched.
- Runs persist finding/edit text, parse JSON with `raw_decode` first, rotate arm order per run, and refuse to summarise mixed models or backends.
- No client names, client code or client material anywhere in the repo.
- Schedule: 03 is built first. If 02 is not green by Mon 5 Oct 2026 noon, Issue 14 ships with 03 only and 02 moves to its own issue.

## Non-goals

- Re-running or fixing folder 01 (its debt stays in the vault series note).
- Agentic, tools-on workers editing files themselves.
- An LLM judge as the scoring oracle, or regex over prose as a check.
- A lead that plans the 03 split.
- Cross-folder or cross-model comparisons.

## Success signal

- Before Tue 6 Oct 2026, `uv run pytest` passes, and the 03 README (plus 02 unless the fallback triggered) carries a measured table from its own `results/runs.jsonl` that Issue 14 quotes, including Thiago's own seconds per conflict.

## Assumptions

- The 02 gate's pass/fail plus reason is enough to attribute a fault to a stage; no separate tracing is needed.

## Open Questions

- Exact numeric needs (bookings-api and notifications timeouts, pricing-engine ceiling, payments-gateway rate) and the predictions: proposed by the fixtures build, approved by Thiago under CAP-10.
