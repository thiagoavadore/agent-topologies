"""Run the arms n times, append every run to results/runs.jsonl and write results/summary.md.

Automated arms (default n=5), who owns the merge when workers collide on platform.yaml:
  first-wins         the first worker commit to touch a key wins, later changes are dropped
  supervisor-merges  Opus writes the merged value for each conflict
  hub-owner          workers send change requests, Opus applies them to platform.yaml
Human arm (`--human`, n=3, needs you at the terminal): you decide each conflict, timed.
Half the runs per automated arm (rounded toward failed) lose one worker; the human arm loses one in three.
"""

import argparse
import json
import os
import statistics
from pathlib import Path

from fanout import ARMS, WORKERS, run_row
from topologies.model import backend_from_name

HERE = Path(__file__).parent
AUTOMATED_ARMS = [arm for arm in ARMS if arm != "human-decides"]
HUMAN_ARM = "human-decides"
HUMAN_RUNS = 3


def failing_run_indexes(arm: str, n: int) -> set[int]:
    """Which 0-based runs lose a worker: every other run starting with the first (rounds toward failed); one for the human arm."""
    if arm == HUMAN_ARM:
        return {1} if n > 1 else {0}
    return set(range(0, n, 2))


def failing_worker(index: int) -> str:
    return list(WORKERS)[index % len(WORKERS)]


def rotated(arms: list[str], index: int) -> list[str]:
    shift = index % len(arms)
    return arms[shift:] + arms[:shift]


def run_arms(n: int, arms: list[str], out: Path, backend_name: str | None, reducer_model: str, worker_model: str) -> None:
    backend_name = backend_name or os.environ.get("TOPOLOGIES_BACKEND", "claude-cli")
    backend = backend_from_name(backend_name)
    out.parent.mkdir(parents=True, exist_ok=True)
    failing = {arm: failing_run_indexes(arm, n) for arm in arms}
    for index in range(n):
        for arm in rotated(arms, index):
            row = run_row(
                run=index + 1,
                backend_name=backend_name,
                worker_model=worker_model,
                reducer_model=reducer_model,
                arm=arm,
                backend=backend,
                fail_worker=failing_worker(index) if index in failing[arm] else None,
            )
            with out.open("a") as handle:
                handle.write(json.dumps(row) + "\n")
            print(
                f"== {arm} run {index + 1}: fixed {row['score']['risks_fixed']}/{row['score']['risks_total']}, "
                f"regressed {len(row['score']['regressed'])}, conflicts {len(row['conflicts'])}, {row['total_tokens']} tokens"
                + (f", failed worker {row['fail_worker']}, named unfixed cards: {bool(row['named_failed_cards'])}" if row["fail_worker"] else ""),
                flush=True,
            )


def load_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def setup_of(row: dict) -> tuple:
    return (row["worker_model"], row["reducer_model"], row["backend"], tuple(row["worker_served_models"]), tuple(row["reducer_served_models"]))


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
        "| arm | n | textual conflicts, mean | contested hub keys, mean | risks fixed of 12, mean (min) | runs with a regression | failed-worker runs | summary names every unfixed card | silent success | tokens, mean (workers / reducer) | wall s excl. human, mean |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for arm in ARMS:
        runs = [row for row in rows if row["arm"] == arm]
        if not runs:
            continue
        failed = [row for row in runs if row["fail_worker"]]
        fixed = [row["score"]["risks_fixed"] for row in runs]
        lines.append(
            f"| {arm} | {len(runs)} | {mean(len(row['textual_conflicts']) for row in runs):.1f} "
            f"| {mean(len(row['conflicts']) for row in runs):.1f} "
            f"| {mean(fixed):.1f} ({min(fixed)}) "
            f"| {sum(1 for row in runs if row['score']['regressed'])} of {len(runs)} "
            f"| {len(failed)} "
            f"| {sum(1 for row in failed if set(row['named_failed_cards']) == set(row['failed_cards']))} of {len(failed)} "
            f"| {sum(1 for row in failed if row['silent_success'])} of {len(failed)} "
            f"| {mean(row['total_tokens'] for row in runs):,.0f} ({mean(row['tokens_by_role']['workers'] for row in runs):,.0f} / {mean(row['tokens_by_role']['reducer'] + row['tokens_by_role']['supervisor_shadow'] for row in runs):,.0f}) "
            f"| {mean(row['wall_seconds'] - row['human_seconds'] for row in runs):.0f} |"
        )
    human_rows = [(row, item) for row in rows if row["arm"] == HUMAN_ARM for item in row["conflicts"]]
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
    parser.add_argument("--n", type=int, default=5, help="runs per automated arm")
    parser.add_argument("--arms", nargs="+", default=AUTOMATED_ARMS, choices=AUTOMATED_ARMS)
    parser.add_argument("--human", action="store_true", help=f"run only the {HUMAN_ARM} arm ({HUMAN_RUNS} runs, interactive)")
    parser.add_argument("--out", type=Path, default=HERE / "results" / "runs.jsonl")
    parser.add_argument("--summary", type=Path, default=HERE / "results" / "summary.md")
    parser.add_argument("--backend", default=None, help="claude-cli (default) or anthropic-sdk")
    parser.add_argument("--reducer-model", default="claude-opus-5-5")
    parser.add_argument("--worker-model", default="claude-sonnet-5-5")
    parser.add_argument("--summarise-only", action="store_true")
    args = parser.parse_args()
    if not args.summarise_only:
        n, arms = (HUMAN_RUNS, [HUMAN_ARM]) if args.human else (args.n, args.arms)
        run_arms(n, arms, args.out, args.backend, args.reducer_model, args.worker_model)
    table = summarise(args.out)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(table + "\n")
    print(table)


if __name__ == "__main__":
    main()
