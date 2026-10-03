"""Run each checkpoint arm n times, append every run to a JSONL file, and write the summary.

Arms:
  none           no checkpoints
  end-only       one checkpoint, after stage 4
  every-handoff  a checkpoint after each of stages 1 to 4

Run i is faulted when i is even (0-based), so n=5 gives 3 faulted and 2 clean runs. The fault of run i is the same in every arm.
Arm order rotates per run.
"""

import argparse
import dataclasses
import datetime as dt
import json
import os
import statistics
from pathlib import Path

from grading import fault_outcome, false_rejection, load_planted, score_plan
from injection import FAULT_KINDS, FAULT_TARGETS, Fault
from pipeline import ARMS, PipelineRecord, load_cards, run_pipeline
from topologies.guards import describe_skipped
from topologies.model import backend_from_name

HERE = Path(__file__).parent
DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_CEILING = 250_000


def fault_for_run(index: int) -> Fault | None:
    """The fault of 0-based run `index`: none on odd runs; kind and target rotate over the faulted ones."""
    if index % 2 == 1:
        return None
    ordinal = index // 2
    card, category = list(FAULT_TARGETS)[ordinal % len(FAULT_TARGETS)]
    return Fault(FAULT_KINDS[ordinal % len(FAULT_KINDS)], card, category)


def arms_for_run(arms: list[str], index: int) -> list[str]:
    shift = index % len(arms)
    return arms[shift:] + arms[:shift]


def run_row(record: PipelineRecord, fault: Fault | None, planted: set, *, run: int, backend: str, model: str) -> dict:
    served = sorted({name for stage in record.stages for name in stage.served_models} | {name for check in record.checkpoints for name in check.served_models})
    return {
        "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "arm": record.arm,
        "run": run,
        "backend": backend,
        "model": model,
        "served_models": served,
        "fault": dataclasses.asdict(fault) if fault else None,
        "injection": dataclasses.asdict(record.injection) if record.injection else None,
        "fault_outcome": fault_outcome(record, fault),
        "false_rejection": false_rejection(record, fault),
        "status": record.status,
        "caught_at_stage": record.caught_at_stage,
        "stage_tokens": record.stage_tokens,
        "gate_tokens": record.gate_tokens,
        "total_tokens": record.total_tokens,
        "wall_seconds": record.wall_seconds,
        "stages": [dataclasses.asdict(stage) for stage in record.stages],
        "checkpoints": [dataclasses.asdict(check) for check in record.checkpoints],
        "plan": record.plan,
        "skipped": describe_skipped(record.skipped),
        "score": score_plan(record.plan, planted) if record.plan is not None else None,
    }


def run_arms(n: int, arms: list[str], out: Path, backend_name: str | None, model: str, ceiling: int | None) -> None:
    backend_name = backend_name or os.environ.get("TOPOLOGIES_BACKEND", "claude-cli")
    backend = backend_from_name(backend_name)
    planted = load_planted()
    cards = load_cards(HERE / "fixtures" / "services")
    out.parent.mkdir(parents=True, exist_ok=True)
    for index in range(n):
        fault = fault_for_run(index)
        for arm in arms_for_run(arms, index):
            record = run_pipeline(cards, backend, arm=arm, model=model, fault=fault, token_ceiling=ceiling)
            row = run_row(record, fault, planted, run=index + 1, backend=backend_name, model=model)
            with out.open("a") as handle:
                handle.write(json.dumps(row) + "\n")
            print(
                f"== {arm} run {index + 1} ({fault.kind if fault else 'clean'}): {row['status']}, "
                f"fault {row['fault_outcome']}, {row['total_tokens']} tokens",
                flush=True,
            )


def load_rows(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    setups = sorted({f"{row['model']} via {row['backend']}" for row in rows})
    if len(setups) > 1:
        raise ValueError(f"{path} mixes models or backends ({', '.join(setups)}); move the other runs aside first")
    return rows


def summarise(path: Path) -> str:
    if not path.exists():
        return f"No runs at {path}."
    rows = load_rows(path)
    lines = [
        "| arm | runs (faulted / clean) | fault outcomes | tokens, mean (min to max) | gate share of tokens | false rejections (clean runs) | recall on final plan, mean (runs with a plan) | wall s, mean |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for arm in ARMS:
        runs = [row for row in rows if row["arm"] == arm]
        if not runs:
            continue
        faulted = [row for row in runs if row["fault"]]
        clean = [row for row in runs if not row["fault"]]
        outcomes = {}
        for row in faulted:
            outcomes[row["fault_outcome"]] = outcomes.get(row["fault_outcome"], 0) + 1
        outcome_text = ", ".join(f"{name} {count}" for name, count in sorted(outcomes.items())) or "none"
        tokens = [row["total_tokens"] for row in runs]
        gate_share = sum(row["gate_tokens"] for row in runs) / sum(tokens) if sum(tokens) else 0
        scored = [row["score"]["recall"] for row in runs if row["score"]]
        recall_text = f"{statistics.mean(scored):.2f} ({len(scored)} of {len(runs)})" if scored else "n/a (0 plans)"
        lines.append(
            f"| {arm} | {len(runs)} ({len(faulted)} / {len(clean)}) | {outcome_text} "
            f"| {statistics.mean(tokens):,.0f} ({min(tokens):,} to {max(tokens):,}) "
            f"| {gate_share:.0%} "
            f"| {sum(row['false_rejection'] for row in clean)} of {len(clean)} "
            f"| {recall_text} "
            f"| {statistics.mean(row['wall_seconds'] for row in runs):.0f} |"
        )
    lines += ["", "Per injected fault:", "", "| run | arm | fault | outcome | stopped by |", "|---|---|---|---|---|"]
    for row in sorted((r for r in rows if r["fault"]), key=lambda r: (r["run"], list(ARMS).index(r["arm"]))):
        fault = row["fault"]
        failing = [c for c in row["checkpoints"] if c["verdict"] != "pass"]
        reason = f"{failing[0]['check']} after stage {failing[0]['after_stage']}: {failing[0]['reason']}" if failing else ""
        lines.append(f"| {row['run']} | {row['arm']} | {fault['kind']} {fault['card']}:{fault['category']} | {row['fault_outcome']} | {reason} |")
    unusual = [f"{row['arm']} run {row['run']}: {row['status']}" for row in rows if row["status"] in ("stage_failed", "checkpoint_error", "over_budget")]
    if unusual:
        lines += ["", "Runs that ended without a verdict: " + "; ".join(unusual)]
    served = sorted({name for row in rows for name in row["served_models"]})
    dates = sorted({row["at"][:10] for row in rows})
    lines.append(f"\nModel: {rows[0]['model']} via {rows[0]['backend']}; served by {', '.join(served) or 'not reported'}. Run dates: {', '.join(dates)}.")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", type=int, default=5)
    parser.add_argument("--arms", nargs="+", default=list(ARMS), choices=list(ARMS))
    parser.add_argument("--out", type=Path, default=HERE / "results" / "runs.jsonl")
    parser.add_argument("--backend", default=None, help="claude-cli (default) or anthropic-sdk")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--ceiling", type=int, default=DEFAULT_CEILING, help="token budget per run; 0 means no limit")
    parser.add_argument("--summarise-only", action="store_true")
    args = parser.parse_args()
    if not args.summarise_only:
        run_arms(args.n, args.arms, args.out, args.backend, args.model, args.ceiling or None)
    summary = summarise(args.out)
    (args.out.parent / "summary.md").write_text(summary + "\n")
    print(summary)


if __name__ == "__main__":
    main()
