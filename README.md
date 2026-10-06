# agent-topologies

When several AI agents work on one job, someone has to decide how the work is split, who handles each part, and who settles disagreements. Those are the same questions a manager answers when organising a team. This repo tests common multi-agent setups by asking those questions, and backs each answer with a real, measured run.

It's the companion code for the series "Agent topologies are org charts" in [The Recovering CTO](https://writing.tilinthecloud.com). Each folder is one setup, runs on its own, and has a README with the question, the experiment and the result.

| Folder | The setup | The question it tests | Newsletter issue |
|---|---|---|---|
| [`01-orchestrator-worker`](01-orchestrator-worker/) | A lead agent splits the job and hands pieces to workers | Does the lead earn its keep, and what happens when it's out? | 13 |
| [`02-pipeline-checkpoint`](02-pipeline-checkpoint/) | Agents work in sequence, with a check between steps | When step 3 fails, whose problem is it? | 14 |
| [`03-fan-out-partial-reducer`](03-fan-out-partial-reducer/) | Many agents work in parallel, one merges their results | Who is responsible for the merge? | 14 |
| `04-hierarchical` | Leads managing other leads | Should the agent layers match the team layers? | 15 (planned) |
| `05-event-bus` | Agents react to messages on a shared channel | Who looks after the shared channel? | 15 (planned) |
| `06-critic-refiner` | One agent writes, another critiques, against a fixed test | Who owns the test, and can the writer game it? | 16 (planned) |
| `07-sidecar-guard` | A rule-based guard checks every action before it runs | Which safety check can no agent argue its way past? | 17 (planned) |

## Setup

```bash
uv sync
uv run pytest            # runs the tests, no model calls
```

There's no agent framework here. Every folder calls the models through one small client, [`src/topologies/model.py`](src/topologies/model.py), so the code is easy to copy into your own setup. It can call the models two ways:

- **Claude Code** (default): runs `claude -p` headless, so it works on a Claude subscription. Tools and plugins are off, so each call is a single model reply. Claude Code adds a fixed prompt of a few hundred tokens to every call and turns on thinking, and both count toward the token numbers.
- **Anthropic API**: uses the Anthropic Python SDK and needs `ANTHROPIC_API_KEY` (or an `ant auth login` profile). Turn it on with `--backend anthropic-sdk` or `TOPOLOGIES_BACKEND=anthropic-sdk`.

Token counts will differ between the two. Every comparison inside a folder uses one backend throughout. Claude Code's overhead is per call, though, so setups that make more calls pay more of it.

## License

MIT
