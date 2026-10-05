"""Run each checkpoint arm n times, append every run to a JSONL file, and write the summary.

Arms:
  none           no checkpoints
  end-only       one checkpoint, after stage 4
  every-handoff  a checkpoint after each of stages 1 to 4

Run i is faulted when i is even (0-based), so n=10 gives 5 faulted and 5 clean runs. The fault of run i is the same in every arm.
Arm order rotates per run. Refuses to run while PREDICTIONS.md says DRAFT.
"""

import argparse
from pathlib import Path

from ckpt_pipeline import ARMS
from ckpt_runs import DEFAULT_CEILING, DEFAULT_MODEL, HERE, assert_preregistered, run_arms, summarise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", type=int, default=10)
    parser.add_argument("--arms", nargs="+", default=list(ARMS), choices=list(ARMS))
    parser.add_argument("--out", type=Path, default=HERE / "results" / "runs.jsonl")
    parser.add_argument("--backend", default=None, help="claude-cli (default) or anthropic-sdk")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--ceiling", type=int, default=DEFAULT_CEILING, help="token budget per run; 0 means no limit")
    parser.add_argument("--summarise-only", action="store_true")
    args = parser.parse_args()
    if not args.summarise_only:
        assert_preregistered()
        run_arms(args.n, args.arms, args.out, args.backend, args.model, args.ceiling or None)
    summary = summarise(args.out)
    (args.out.parent / "summary.md").write_text(summary + "\n")
    print(summary)


if __name__ == "__main__":
    main()
