# 01 · Orchestrator-worker

## The question

In an orchestrator-worker setup, one "lead" agent splits a job into pieces and hands them to worker agents. The lead is the expensive part: it runs on the biggest model, and its planning is an extra call on top of the workers'. So does it earn its keep? And what happens to the team when the lead is out?

## The short answer

On this task, no. Splitting the work alphabetically, with no lead at all, found the same risks at the same cost as letting the lead decide. What did cut tokens was capping the number of workers: two workers instead of four used 36% fewer tokens and missed one risk out of 60 (12 risks, five runs).

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
3. **No lead plan:** the lead doesn't plan, and the cards are split alphabetically between two workers. The lead still writes the final summary.

## Results

Run on 25 Sep 2026. Tokens count everything sent and received across the whole review.

| Version | Workers | Tokens per review (average) | Risks found |
|---|---|---|---|
| Uncapped | 4 | 36,573 | 60 of 60 |
| Capped at two | 2 | 23,278 | 59 of 60 |
| No lead plan, alphabetical | 2 | 22,994 | 59 of 60 |

What this tells us:

- **Fewer workers is much cheaper.** Two workers used about a third fewer tokens than four. Apart from one undercounted run (below), every four-worker run used more than every two-worker run.
- **The lead's plan made no measurable difference.** An alphabetical split did just as well. The only thing the lead added was its own planning call, about 1,500 tokens.
- **More workers also means more noise.** The four-worker version reported 3.6 findings per review on average that weren't on the answer key, against about one for the two-worker versions. Some of those were fair points, some weren't.
- **The missed risk is the same one.** Both two-worker versions missed it once: the `smtplib` call with no timeout in `notifications.md`. With five runs that could be chance, or a risk that's easier to miss when a worker has more cards.

One run is undercounted. In the first uncapped run, one worker was recorded at 0 tokens even though it returned four findings, so that run's 26,379 is too low. The real uncapped average is higher, which makes the gap wider, not narrower. The client now warns when a call reports zero tokens.

Raw data: [`results/summary.md`](results/summary.md) for the full table, [`results/runs.jsonl`](results/runs.jsonl) for every run (the lead's plans are in each worker's `focus`), [`results/trace.log`](results/trace.log) for the progress log of each run.

## The answer key was wrong the first time

In the first 15 runs, one planted risk was an `httpx` call with no timeout. But `httpx` has a 5-second timeout by default, so there was no risk there. The two-worker versions that "missed" it were right, and the four-worker version got credit for a false alarm. The fixture now uses a `requests` call, which really does wait forever, and everything was re-run. The old runs are kept in [`results/2026-09-25-v1-invalid-ground-truth.jsonl`](results/2026-09-25-v1-invalid-ground-truth.jsonl).

The lesson: whoever writes the answer key decides the result.

One smaller change after that: the fake key in `payments-gateway.md` started with `sk_live_`, which GitHub blocks as a real Stripe key. It now starts with `cp_live_`. One check run per version still found all 12 risks ([`results/2026-09-25-key-prefix-check.jsonl`](results/2026-09-25-key-prefix-check.jsonl)).

## Limits

- One small task, five runs per version.
- These are token counts, not cost. Cached input is counted like fresh input, though it's billed at about a tenth of the price.
- The token counts include Claude Code's fixed prompt and thinking (see the [root README](../README.md#setup)). That overhead is per call, so the four-worker version pays more of it, and the gap may shrink through the Anthropic API (`--backend anthropic-sdk`).
- These workers only read and report. Workers that change things are the next experiment.

## Run it yourself

```bash
uv sync
uv run pytest                                          # tests every safety check, no model calls
uv run 01-orchestrator-worker/run.py                   # one review, up to 3 workers (not one of the versions above)
uv run 01-orchestrator-worker/run.py --cap 0           # no limit on workers
uv run 01-orchestrator-worker/run.py --break-router    # skip the lead's plan
uv run 01-orchestrator-worker/experiment.py --n 5      # the full experiment, written to results/local-runs.jsonl
```

## What's in the code

Everything lives in [`orchestrator.py`](orchestrator.py). Besides the lead and the workers, it has four safety checks that any real setup needs:

| Check | What it does | Where |
|---|---|---|
| Strict format | A worker's report must follow the JSON format exactly. A bad report gets one retry, then it's thrown out and its cards are listed as "not reviewed". Findings about cards the worker wasn't given are dropped. | `FINDINGS_SCHEMA` |
| Worker cap | If the lead plans more workers than allowed, its plan is squeezed into fewer workers. No card gets dropped. | `apply_fan_out_cap` |
| Token budget | Workers run up to four at a time. Before each group starts, check the spend against the budget (60,000 tokens per review by default). Once over it, the remaining cards are skipped and named in the summary instead of silently lost. A group that starts under budget can finish over it. | `dispatch` |
| Backup plan | If the lead fails or returns a bad plan, split the cards alphabetically instead. A failed lead never stops the review. | `fallback_plan` |

No worker report broke the format in the real runs, so the retry and throw-out paths are covered only by the tests.

The code and results use a few different names from this README:

| README | Code and results |
|---|---|
| version (Uncapped, Capped at two, No lead plan) | arm (`uncapped`, `cap2`, `router-killed`) |
| the lead's plan / its CTO summary | the router call / the synthesis call, both on `supervisor_model` |
| card | file (`files`) |
| worker cap / token budget / group of workers | fan-out cap / token ceiling / wave |
| risks found / findings not on the answer key | recall / extra findings |
