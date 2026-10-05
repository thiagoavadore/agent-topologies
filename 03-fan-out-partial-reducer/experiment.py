"""Run the arms n times, append every run to results/runs.jsonl and write results/summary.md.

Automated arms (default n=10), who owns the merge when workers collide on platform.yaml:
  first-wins         the first worker commit to touch a key wins, later changes are dropped
  supervisor-merges  Opus writes the merged value for each conflict
  hub-owner          workers send change requests, Opus applies them to platform.yaml
  overrides-allowed  baseline 1: workers may write their own service overrides and code literals, hub conflicts first-wins
  code-local         baseline 2: no overrides, but timeouts as code literals are allowed, hub conflicts first-wins
The first three and the human arm are platform-mandated: no worker overrides (the harness strips them), the timeout in
code must be http_timeout(...) (a file that breaks it is reverted), and only the merge owner may grant an override.
Human arm (`--human`, n=3 by default, `--human-n 5` for five; needs you at the terminal): you decide each conflict, timed.
Half the runs per automated arm (rounded toward failed) lose one worker, rotating; the human arm loses the middle run
of 3 (runs 2 and 4 of 5). Benchmark runs are refused while PREDICTIONS.md still says DRAFT.
"""

import argparse
import json
import os
import statistics
from pathlib import Path

from fanout_config import ARMS, AUTOMATED_ARMS, HUMAN_ARM, REDUCER_MODEL, WORKER_MODEL, WORKERS
from fanout import run_row
from topologies.guards import SkippedWork, describe_skipped, may_start_group
from topologies.harbour import HUB_KEYS, RISKS
from topologies.model import backend_from_name

HERE = Path(__file__).parent
PREDICTIONS = HERE.parent / "PREDICTIONS.md"
DEFAULT_N = 10
HUMAN_N = 3


def refuse_unregistered(path: Path) -> None:
    """Pre-registration: no benchmark run while the predictions are a draft (or missing)."""
    if not path.exists():
        raise SystemExit(f"Refusing to run: {path} is missing. Commit approved predictions before the benchmark.")
    if "DRAFT" in path.read_text(encoding="utf-8"):
        raise SystemExit(f"Refusing to run: {path.name} still says DRAFT. Get the predictions approved and committed first (run.py is still allowed).")


def failing_run_indexes(arm: str, n: int) -> list[int]:
    """Which 0-based runs lose a worker: automated arms every other run from the first (rounds toward failed); human arm the middle run (runs 2 and 4 of 5)."""
    if arm == HUMAN_ARM:
        return [1] if n < 5 else [1, 3]
    return list(range(0, n, 2))


def failing_worker(rank: int) -> str:
    """Rotate through the workers by the order of the failing runs, so each worker fails about equally often."""
    return list(WORKERS)[rank % len(WORKERS)]


def rotated(arms: list[str], index: int) -> list[str]:
    shift = index % len(arms)
    return arms[shift:] + arms[:shift]


def run_arms(n: int, arms: list[str], out: Path, backend_name: str | None, reducer_model: str, worker_model: str, token_ceiling: int | None = None) -> None:
    backend_name = backend_name or os.environ.get("TOPOLOGIES_BACKEND", "claude-cli")
    backend = backend_from_name(backend_name)
    out.parent.mkdir(parents=True, exist_ok=True)
    failing = {arm: failing_run_indexes(arm, n) for arm in arms}
    spent, skipped = 0, []
    for index in range(n):
        for arm in rotated(arms, index):
            if not may_start_group(spent, token_ceiling):
                skipped.append(SkippedWork(f"{arm} run {index + 1}", f"token ceiling {token_ceiling} reached at {spent}"))
                continue
            fail = failing_worker(failing[arm].index(index)) if index in failing[arm] else None
            row = run_row(run=index + 1, backend_name=backend_name, worker_model=worker_model, reducer_model=reducer_model, arm=arm, backend=backend, fail_worker=fail)
            spent += row["total_tokens"]
            with out.open("a") as handle:
                handle.write(json.dumps(row) + "\n")
            score = row["score"]
            print(
                f"== {arm} run {index + 1}: fixed {score['risks_fixed']}/{score['risks_total']}, regressions {score['regressions']}, "
                f"outside contract {score['outside_contract']}, conflicts {len(row['conflicts'])}, {row['total_tokens']} tokens"
                + (f", failed worker {row['fail_worker']}, named its services: {bool(row['named_failed_services'])}" if row["fail_worker"] else ""),
                flush=True,
            )
    if skipped:
        print(describe_skipped(skipped), flush=True)


def load_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def setup_of(row: dict) -> tuple:
    return (row["worker_model"], row["reducer_model"], row["backend"], tuple(row["worker_served_primary"]), tuple(row["reducer_served_primary"]))


def mean(values) -> float:
    values = list(values)
    return statistics.mean(values) if values else 0.0


def summarise(path: Path) -> str:
    if not path.exists():
        return f"No runs at {path}."
    rows = load_rows(path)
    setups = {setup_of(row) for row in rows}
    if len(setups) > 1:
        raise ValueError(f"{path.name} mixes models or backends ({len(setups)} setups); summarise one setup per file")
    worker_model, reducer_model, backend, worker_served, reducer_served = setups.pop()
    lines = [
        f"| arm | n | git textual conflicts, mean | contested hub keys, mean | risks fixed of {len(RISKS)}, mean (min) | regressions, mean (runs with any) | outside contract, mean | failed-worker runs | report names the failed services | silent success | tokens, mean (workers / reducer / report / shadow) | wall s excl. human, mean |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    by_arm = {arm: [row for row in rows if row["arm"] == arm] for arm in ARMS}
    for arm, runs in by_arm.items():
        if not runs:
            continue
        failed = [row for row in runs if row["fail_worker"]]
        fixed = [row["score"]["risks_fixed"] for row in runs]
        roles = [row["tokens_by_role"] for row in runs]
        lines.append(
            f"| {arm} | {len(runs)} | {mean(len(row['textual_conflicts']) for row in runs):.1f} "
            f"| {mean(len(row['conflicts']) for row in runs):.1f} "
            f"| {mean(fixed):.1f} ({min(fixed)}) "
            f"| {mean(row['score']['regressions'] for row in runs):.1f} ({sum(1 for row in runs if row['score']['regressions'])} of {len(runs)}) "
            f"| {mean(row['score']['outside_contract'] for row in runs):.1f} "
            f"| {len(failed)} "
            f"| {sum(1 for row in failed if set(row['named_failed_services']) == set(row['failed_services']))} of {len(failed)} all, "
            f"{sum(1 for row in failed if row['named_failed_services'])} of {len(failed)} some "
            f"| {sum(1 for row in failed if row['silent_success'])} of {len(failed)} "
            f"| {mean(row['total_tokens'] for row in runs):,.0f} ({mean(r['workers'] for r in roles):,.0f} / {mean(r['reducer'] for r in roles):,.0f} / {mean(r['report'] for r in roles):,.0f} / {mean(r['supervisor_shadow'] for r in roles):,.0f}) "
            f"| {mean(row['wall_seconds'] - row['human_seconds'] for row in runs):.0f} |"
        )
    present = [arm for arm, runs in by_arm.items() if runs]
    lines += ["", "Service overrides (who wrote them, and how far the platform forked):", "", "| arm | mandate rejections (file edits reverted), mean | worker override edits stripped, mean | worker override edits written, mean | granted by the merge owner, mean | services with an override in the merged repo, mean | distinct effective timeouts across the 8 services, mean |", "|---|---|---|---|---|---|---|"]
    for arm in present:
        runs = by_arm[arm]
        lines.append(
            f"| {arm} | {mean(row['mandate_rejections'] for row in runs):.1f} | {mean(row['override_edits_stripped'] for row in runs):.1f} | {mean(row['override_edits_written'] for row in runs):.1f} "
            f"| {mean(len(row['overrides_granted']) for row in runs):.1f} | {mean(len(row['overrides_in_merged']) for row in runs):.1f} "
            f"| {mean(row['distinct_timeouts'] for row in runs):.1f} |"
        )
    lines += ["", "Runs in which each hub key was contested (two workers, different values):", "", "| hub key | " + " | ".join(present) + " |", "|---|" + "---|" * len(present)]
    for key in HUB_KEYS:
        cells = [f"{sum(1 for row in by_arm[arm] if key in row['conflicted_keys'])} of {len(by_arm[arm])}" for arm in present]
        lines.append(f"| {key} | " + " | ".join(cells) + " |")
    human_rows = [(row, item) for row in by_arm[HUMAN_ARM] for item in row["conflicts"]]
    if human_rows:
        lines += ["", "Human decisions, one row per conflict:", "", "| run | key | human choice | human value | seconds | supervisor value | same value |", "|---|---|---|---|---|---|---|"]
        for row, item in human_rows:
            same = item["value"].strip().lower() == item["supervisor_value"].strip().lower()
            lines.append(f"| {row['run']} | {item['key']} | {item['human_choice']} | {item['value']} | {item['human_seconds']} | {item['supervisor_value']} | {'yes' if same else 'no'} |")
        lines.append(f"\nHuman seconds per conflict, mean: {mean(item['human_seconds'] for _, item in human_rows):.1f}.")
    dates = sorted({row["at"][:10] for row in rows})
    lines.append(
        f"\nWorkers {worker_model} (served: {', '.join(worker_served) or 'not reported'}), reducer {reducer_model} "
        f"(served: {', '.join(reducer_served) or 'not reported'}), backend {backend}. Run dates: {', '.join(dates)}."
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", type=int, default=DEFAULT_N, help="runs per automated arm")
    parser.add_argument("--arms", nargs="+", default=list(AUTOMATED_ARMS), choices=AUTOMATED_ARMS)
    parser.add_argument("--human", action="store_true", help=f"run only the {HUMAN_ARM} arm (interactive)")
    parser.add_argument("--human-n", type=int, choices=(3, 5), default=HUMAN_N, help="runs of the human arm")
    parser.add_argument("--token-ceiling", type=int, default=None, help="stop starting new runs once this many tokens are spent")
    parser.add_argument("--out", type=Path, default=HERE / "results" / "runs.jsonl")
    parser.add_argument("--summary", type=Path, default=HERE / "results" / "summary.md")
    parser.add_argument("--backend", default=None, help="claude-cli (default) or anthropic-sdk")
    parser.add_argument("--reducer-model", default=REDUCER_MODEL)
    parser.add_argument("--worker-model", default=WORKER_MODEL)
    parser.add_argument("--summarise-only", action="store_true")
    args = parser.parse_args()
    if not args.summarise_only:
        refuse_unregistered(PREDICTIONS)
        n, arms = (args.human_n, [HUMAN_ARM]) if args.human else (args.n, args.arms)
        run_arms(n, arms, args.out, args.backend, args.reducer_model, args.worker_model, args.token_ceiling)
    table = summarise(args.out)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(table + "\n")
    print(table)


if __name__ == "__main__":
    main()
