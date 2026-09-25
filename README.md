# agent-topologies

Multi-agent topologies read as org charts. One folder per topology, each runnable, each with a number from a real run.

Companion code for the series "Agent topologies are org charts" in [The Recovering CTO](https://writing.tilinthecloud.com). The series sorts patterns by **who owns the split**, meaning who decomposes the work, who owns each boundary, and who breaks a tie, rather than by the shape of the diagram.

| Folder | Topology | Org question | Issue |
|---|---|---|---|
| [`01-orchestrator-worker`](01-orchestrator-worker/) | Supervisor splits, workers execute against a strict contract | Who owns the decomposition, and what happens when they are out? | 13 |
| `02-pipeline-checkpoint` | Sequential pipeline with checkpoints | Who owns stage N's failure? | 14 (planned) |
| `03-fan-out-partial-reducer` | Fan-out / fan-in with a partial reducer | Who owns the merge? | 14 (planned) |
| `04-hierarchical` | Multi-tier hierarchy | Are domain boundaries team boundaries? | 15 (planned) |
| `05-event-bus` | Event-driven bus | Who owns the bus as a product? | 15 (planned) |
| `06-critic-refiner` | Critic-refiner with a protected oracle | Who owns the oracle? | 16 (planned) |
| `07-sidecar-guard` | Sidecar guard with a deterministic risk table | Which guard can nobody talk past? | 17 (planned) |

## Setup

```bash
uv sync
uv run pytest            # deterministic tests, no model calls
```

Every folder uses the same thin client in [`src/topologies/model.py`](src/topologies/model.py), no agent framework, so you can map it onto your own harness. Two backends:

- `claude-cli` (default): headless Claude Code (`claude -p`), which runs on a Claude subscription. Tools, settings and MCP are off, so each call is one model turn. Claude Code adds a fixed prompt of a few hundred input tokens per call and turns thinking on for its models, and both show up in the token counts.
- `anthropic-sdk`: the Anthropic Python SDK. Needs `ANTHROPIC_API_KEY`. Select it with `--backend anthropic-sdk` or `TOPOLOGIES_BACKEND=anthropic-sdk`.

Absolute token numbers depend on the backend. Comparisons inside a folder always hold the backend constant.

## License

MIT
