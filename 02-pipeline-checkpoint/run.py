"""Run one pipeline review and print each stage, each checkpoint and the fate of the injected fault.

Use `--out` to append the full run record to a scratch file; never point it at results/runs.jsonl.
"""

import argparse
import json
from pathlib import Path

from ckpt_faults import FAULT_KINDS, FAULT_TARGETS, Fault
from ckpt_pipeline import ARMS, load_world, run_pipeline
from ckpt_runs import DEFAULT_CEILING, DEFAULT_MODEL, run_row
from ckpt_scoring import fault_outcome, score_plan
from topologies.guards import describe_skipped
from topologies.model import backend_from_name


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=list(ARMS), default="every-handoff")
    parser.add_argument("--fault", choices=[*FAULT_KINDS, "none"], default="wrong-service")
    parser.add_argument("--target", choices=list(FAULT_TARGETS), default=FAULT_TARGETS[0], help="risk ID to attack")
    parser.add_argument("--backend", default=None, help="claude-cli (default) or anthropic-sdk")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--ceiling", type=int, default=DEFAULT_CEILING, help="token budget per run; 0 means no limit")
    parser.add_argument("--out", type=Path, default=None, help="append the full run record to this JSONL file")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    fault = None if args.fault == "none" else Fault(args.fault, args.target)
    record = run_pipeline(
        load_world(),
        backend_from_name(args.backend),
        arm=args.arm,
        model=args.model,
        fault=fault,
        token_ceiling=args.ceiling or None,
    )
    for stage in record.stages:
        print(f"stage {stage.stage} {stage.name}: {stage.status}, {stage.tokens} tokens, {stage.wall_seconds}s, {stage.served_models}")
        if stage.error:
            print(f"  error: {stage.error}")
    if record.injection:
        print(f"injection: {record.injection}")
    for check in record.checkpoints:
        print(f"checkpoint after stage {check.after_stage} ({check.check}): {check.verdict}, {check.reason}")
    print(f"\nstatus {record.status}, caught at stage {record.caught_at_stage}, tokens {record.total_tokens} (gates {record.gate_tokens})")
    print(f"fault outcome: {fault_outcome(record, fault)}")
    if record.plan is not None:
        result = score_plan(record.plan)
        print(f"recall {result['grounded']}/{result['risks']}, missed: {', '.join(result['missed']) or 'none'}")
    print(describe_skipped(record.skipped))
    if args.out:
        row = run_row(record, fault, run=1, backend=args.backend or "claude-cli", model=args.model)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("a") as handle:
            handle.write(json.dumps(row) + "\n")


if __name__ == "__main__":
    main()
