# 01 · Orchestrator-worker

**Org question:** who owns the decomposition, and what happens when they are out?

A supervisor (the team lead) splits a risk review of eight service cards across workers, dispatches them, validates every returned payload against a strict JSON contract, and writes one summary for the CTO. The workers never talk to each other. In Team Topologies terms each worker is X-as-a-Service: a fixed input, a fixed output, no negotiation.

## What the code shows

- **Router** (`route`): the supervisor sees only a one-paragraph description of each card and returns a plan: how many workers, which cards each gets, and a focus line.
- **Strict contract** (`FINDINGS_SCHEMA`): every finding must name a card, one of eight categories, a severity and a quoted line. A payload that breaks the contract gets one retry, then it is not merged, and its cards are reported as not reviewed. Findings about cards outside the worker's brief are dropped and counted.
- **Fan-out cap** (`apply_fan_out_cap`): if the router plans more workers than the cap, its subtasks are merged round-robin into `cap` workers. No card is dropped.
- **Token ceiling** (`dispatch`): checked before each wave of workers. Once it is crossed, the remaining subtasks are skipped and named in the summary instead of silently disappearing.
- **Deterministic fallback** (`fallback_plan`): if the router fails, returns an invalid plan, or names a card that does not exist, the cards are split alphabetically into contiguous chunks. The review never blocks on the lead.

The fixtures are eight fictional service cards (`fixtures/services/`) with twelve planted risks (`fixtures/planted.json`). Scoring matches exactly on (card, category).

## Run it

```bash
uv sync
uv run pytest                                          # every guard, scripted model, no network
uv run 01-orchestrator-worker/run.py                   # one review, cap 3, trace on stderr
uv run 01-orchestrator-worker/run.py --cap 0           # no fan-out cap
uv run 01-orchestrator-worker/run.py --break-router    # kill the lead, watch the fallback
uv run 01-orchestrator-worker/experiment.py --n 5      # the three arms below
```

## Result

Three arms, five runs each, interleaved, on 25 Sep 2026. Supervisor `claude-opus-5`, workers `claude-haiku-4-5`, both via headless Claude Code (`claude -p`). Tokens are input plus output across every call in the review, including Claude Code's fixed per-call prompt and the thinking tokens it turns on.

| arm | n | workers | tokens per review, mean (min to max) | planted risks found |
|---|---|---|---|---|
| uncapped: the router's plan as is | 5 | 4 | 36,573 (26,379 to 41,999) | 60 of 60 |
| fan-out cap of 2 | 5 | 2 | 23,278 (19,874 to 26,261) | 59 of 60 |
| router killed, alphabetical split into 2 | 5 | 2 | 22,994 (18,538 to 26,897) | 59 of 60 |

- Capping fan-out at two workers cut tokens per review by 36% and missed one planted risk in 60.
- Killing the router and splitting the cards alphabetically cost the same as the capped router and found the same number of risks. On this task, the lead's decomposition bought nothing measurable. Its measurable cost was the routing call itself, about 1,500 tokens.
- The uncapped arm also reported more findings outside the planted set (3.6 per review against 1.0 and 0.8). Some are defensible, some are noise. Four workers produce more of both.
- No payload broke the contract in any run, so the retry and not-merged paths are covered by the tests, not by this run.

Full table: [`results/summary.md`](results/summary.md). Every run, with each worker's brief: [`results/runs.jsonl`](results/runs.jsonl). Supervisor and worker trace: [`results/trace.log`](results/trace.log).

### The ground truth was wrong the first time

The first 15 runs ([`results/2026-09-25-v1-invalid-ground-truth.jsonl`](results/2026-09-25-v1-invalid-ground-truth.jsonl)) planted a missing timeout on an `httpx.Client()` call. `httpx` defaults to a 5-second timeout, so there was no risk to find. The capped workers that "missed" it in four of five runs were right, and the uncapped arm got credit for a false positive. The fixture now uses `requests.post` without a timeout, which does wait forever, and the table above is from a full re-run. The lesson belongs in the series too: whoever owns the answer key owns the result.

One change after that re-run: the fake card-processor key in `payments-gateway.md` started with `sk_live_`, which GitHub's push protection blocks as a Stripe key. It now starts with `cp_live_`. One check run per arm on the changed fixture found all 12 planted risks ([`results/2026-09-25-key-prefix-check.jsonl`](results/2026-09-25-key-prefix-check.jsonl)).

## Limits

- n = 5 per arm, one task, eight small cards. The token difference is well outside the run-to-run spread. The recall difference (one risk in 60) is not.
- Absolute tokens depend on the backend. With `--backend anthropic-sdk` there is no Claude Code prompt overhead and Haiku runs without thinking, so expect lower totals. Comparisons between arms hold the backend constant.
- The workers here read and report. Workers that write, each in an isolated workspace, are the next variation.
