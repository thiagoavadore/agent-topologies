# 01 · Orchestrator-worker

## The question

In an orchestrator-worker setup, one "lead" agent splits a job into pieces and hands them to worker agents. The lead is the expensive part: it runs on the biggest model and makes an extra call. So does it earn its keep? And what happens to the team when the lead is out?

## The short answer

On this task, no. Splitting the work alphabetically, with no lead at all, found the same risks at the same cost as letting the lead decide. What did save money was capping the number of workers: two workers instead of four cost 36% fewer tokens and missed one risk in 60.

## The experiment

The agents do a risk review:

- **The input:** eight short, fictional service descriptions ("cards") in [`fixtures/services/`](fixtures/services/). Twelve known risks are hidden in them, like a hardcoded key or an HTTP call with no timeout. The answer key is [`fixtures/planted.json`](fixtures/planted.json).
- **The lead** (Claude Opus) reads a one-paragraph summary of each card and decides how many workers to use and which cards each one gets.
- **The workers** (Claude Haiku) each review their cards and report findings in a fixed JSON format. They never talk to each other.
- **The lead** then merges the findings into one summary for the CTO.
- **Scoring:** a risk counts as found only if a worker names the right card and the right category.

Three versions, five runs each:

1. **Uncapped:** the lead splits the work however it likes. It chose four workers every time.
2. **Capped at two:** the lead still plans the split, but its plan gets squeezed into two workers.
3. **No lead:** the lead is switched off and the cards are split alphabetically between two workers.

## Results

Run on 25 Sep 2026. Tokens count everything sent and received across the whole review.

| Version | Workers | Tokens per review (average) | Risks found |
|---|---|---|---|
| Uncapped | 4 | 36,573 | 60 of 60 |
| Capped at two | 2 | 23,278 | 59 of 60 |
| No lead, alphabetical | 2 | 22,994 | 59 of 60 |

What this tells us:

- **Fewer workers is much cheaper.** Two workers cost about a third less than four, and runs varied little enough that the difference is real.
- **The lead's plan made no measurable difference.** An alphabetical split did just as well. The only thing the lead added was its own planning call, about 1,500 tokens.
- **More workers also means more noise.** The four-worker version reported about 3.6 findings per review that weren't on the answer key, against about one for the two-worker versions. Some of those were fair points, some weren't.
- **The one missed risk proves nothing.** One miss in 60, with five runs per version, could easily be chance.

Raw data: [`results/summary.md`](results/summary.md) for the full table, [`results/runs.jsonl`](results/runs.jsonl) for every run, [`results/trace.log`](results/trace.log) for what the lead and workers actually said.

## The answer key was wrong the first time

In the first 15 runs, one planted risk was an `httpx` call with no timeout. But `httpx` has a 5-second timeout by default, so there was no risk there. The two-worker versions that "missed" it were right, and the four-worker version got credit for a false alarm. The fixture now uses a `requests` call, which really does wait forever, and everything was re-run. The old runs are kept in [`results/2026-09-25-v1-invalid-ground-truth.jsonl`](results/2026-09-25-v1-invalid-ground-truth.jsonl).

The lesson: whoever writes the answer key decides the result.

One smaller change after that: the fake key in `payments-gateway.md` started with `sk_live_`, which GitHub blocks as a real Stripe key. It now starts with `cp_live_`. One check run per version still found all 12 risks ([`results/2026-09-25-key-prefix-check.jsonl`](results/2026-09-25-key-prefix-check.jsonl)).

## Limits

- One small task, five runs per version. Trust the cost difference, not the one missed risk.
- The token counts include overhead from running the models through Claude Code (`claude -p`), which adds a fixed prompt to every call and turns on thinking. Through the Anthropic API directly (`--backend anthropic-sdk`) the numbers will be lower, but the comparison between versions still holds.
- These workers only read and report. Workers that change things are the next experiment.

## Run it yourself

```bash
uv sync
uv run pytest                                          # tests every safety check, no model calls
uv run 01-orchestrator-worker/run.py                   # one review, up to 3 workers
uv run 01-orchestrator-worker/run.py --cap 0           # no limit on workers
uv run 01-orchestrator-worker/run.py --break-router    # switch the lead off
uv run 01-orchestrator-worker/experiment.py --n 5      # the full three-version experiment
```

## What's in the code

Everything lives in [`orchestrator.py`](orchestrator.py). Besides the lead and the workers, it has four safety checks that any real setup needs:

| Check | What it does | Where |
|---|---|---|
| Strict format | A worker's report must follow the JSON format exactly. A bad report gets one retry, then it's thrown out and its cards are listed as "not reviewed". Findings about cards the worker wasn't given are dropped. | `FINDINGS_SCHEMA` |
| Worker cap | If the lead plans more workers than allowed, its plan is squeezed into fewer workers. No card gets dropped. | `apply_fan_out_cap` |
| Token budget | Before each batch of workers, check the spend. Once over budget, the remaining work is skipped and named in the summary instead of silently lost. | `dispatch` |
| Backup plan | If the lead fails or returns a bad plan, split the cards alphabetically instead. The review never waits on the lead. | `fallback_plan` |

No worker report broke the format in the real runs, so the retry and throw-out paths are covered only by the tests.
