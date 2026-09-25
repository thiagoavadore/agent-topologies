"""Run one review and print the trace, the score and the CTO summary."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from orchestrator import Guards, run_review
from scoring import load_planted, score
from topologies.model import backend_from_name

HERE = Path(__file__).parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cap", type=int, default=3, help="fan-out cap; 0 disables it")
    parser.add_argument("--ceiling", type=int, default=60_000, help="token ceiling per request; 0 disables it")
    parser.add_argument("--break-router", action="store_true", help="kill the router to exercise the fallback plan")
    parser.add_argument("--backend", default=None, help="claude-cli (default) or anthropic-sdk")
    parser.add_argument("--supervisor-model", default="claude-opus-5")
    parser.add_argument("--worker-model", default="claude-haiku-4-5")
    parser.add_argument("--json", action="store_true", help="print the full run record as JSON")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    guards = Guards(fan_out_cap=args.cap or None, token_ceiling=args.ceiling or None)
    record = run_review(
        HERE / "fixtures" / "services",
        backend_from_name(args.backend),
        supervisor_model=args.supervisor_model,
        worker_model=args.worker_model,
        guards=guards,
        break_router=args.break_router,
    )
    result = score(record.findings, load_planted())
    if args.json:
        print(json.dumps({"record": asdict(record), "score": result}, indent=2))
        return
    print(f"\nrecall {result['planted_found']}/{result['planted']}, extra {result['extra_findings']}, tokens {record.total_tokens} {record.tokens_by_role}")
    if result["missed"]:
        print(f"missed: {', '.join(result['missed'])}")
    print(f"\n{record.summary}")


if __name__ == "__main__":
    main()
