# Experiment design: folders 02 and 03

## Shared world: Harbour Bikes as real files (02 and 03)

- Same eight services and 12 planted risks as 01, rebuilt as a mini-repo under `fixtures/harbour-bikes/` (one shared home; 01 keeps its markdown cards).
- Per service: real code (e.g. `bookings_api.py` with the actual `requests.post`), real dependency file (`requirements.txt`, `package.json`, `Cargo.toml`, `go.mod`), real network config where relevant, and `service.yaml` with structured fields: `owner`, `instances`, `backup`, numeric `needs`, `overrides`.
- `platform.yaml` is the hub. Start values: `http.default_timeout: 300ms`, `http.default_rate_limit: none`, `backup.policy: none`, `availability.min_instances: 1`.
- Numeric needs (values proposed by the fixtures build, approved under CAP-10): bookings-api payment call timeout at least the gateway's worst case (4 s timeout, 2 retries); notifications SMTP timeout above 300 ms; pricing-engine inherits the hub timeout and needs at most 300 ms; payments-gateway needs at least 20 req/s per caller.
- Risk catalogue with stable IDs (service, category, file, locator) in `src/topologies/`, used by both folders' scoring.

## Shared checker (CAP-9)

- One check per planted risk, by parsing or executing files: timeouts via AST resolved to a number (plus a behavioural test against a slow local stub server); pins via `packaging` and npm semver rules; secret absent and read from the environment; network rule parsed; single point of failure, backup and owner from `service.yaml`; needs compared numerically against the effective value (service override, else hub). No regex over prose.
- `missing_timeout` counts as fixed only when the effective timeout meets that service's need.
- Must accept a reference fixed repo (12 of 12, 0 regressions), score the unchanged repo 0 fixed, and reject a fake-fix suite: `timeout=None`, an undefined timeout variable, a renamed CIDR still open to the world, `latest` behind a variable, a secret moved to another file, an override line with an unparseable value, and others the build finds.

## Pre-registration (CAP-10)

`PREDICTIONS.md` (expected direction per arm and metric), the checker and the fake-fix suite are committed and approved by Thiago before the first benchmark run. A later checker change needs a dated note in the README and a full re-run.

## 02-pipeline-checkpoint

Org question: when stage 3 fails, whose problem is it?

| Item | Decision |
|---|---|
| Stages | 1 extract facts per service → 2 classify risks → 3 prioritise → 4 remediation plan. All Sonnet 5.5. |
| Arms | `none` (no checkpoints), `end-only` (one checkpoint after stage 4), `every-handoff` (checkpoint after stages 1, 2, 3, 4) |
| Checkpoint | Schema contract check (valid JSON, every service accounted for), then a Sonnet 5.5 gate given the stage's input and output, returning pass/fail plus reason. A failure is logged with the owning stage. |
| Fault injection | Harness-side, into the stage-1 output only. Stage-1 facts are keyed by service and field, so a fault targets an ID exactly. Two types, one per faulted run, rotated: (i) a planted risk's fact attributed to the wrong service, (ii) a fact dropped. |
| Runs | n=10 per arm; faulted and clean runs alternate (half and half, rounding toward faulted). Arm order rotates per run. |
| Metrics | Per injected fault: catch stage or `reached-production`. Per arm: tokens (total, gate share), false rejections on clean runs, evidence-grounded recall on the final plan, measured only on runs that reach stage 4 (a finding counts only if it names the right risk ID and cites the field or line). |

## 03-fan-out-partial-reducer

Org question: who is responsible for the merge?

### Task and workspace

- Workers fix the risks on their services. Each worker runs in its own git worktree of a scratch copy of `fixtures/harbour-bikes/`.
- Worker call is single-reply: it returns full new contents for each service file it changes, and hub changes as structured `{key, value, reason}` that the harness applies to `platform.yaml`. The harness writes and commits.
- Workers are Sonnet 5.5. The supervisor/reducer is Opus 5.5 and never plans the split.

### Hub keys and which risks want them

| Hub key | Risks that want it |
|---|---|
| `http.default_timeout` | bookings-api `missing_timeout`, notifications `missing_timeout` (customer-profiles already cites a shared client 2 s/5 s) |
| `http.default_rate_limit` | bookings-api `no_rate_limit` |
| `backup.policy` | customer-profiles `missing_backup`, maintenance-scheduler `missing_backup` |
| `availability.min_instances` | fleet-telemetry `single_point_of_failure`, maintenance-scheduler `single_point_of_failure` |

Service-local only: payments-gateway `hardcoded_secret`, fleet-telemetry `no_owner`, admin-console `public_exposure`, and both `unpinned_dependency` risks (pins live in each manifest).

### Fixed split (draft, built to collide)

| Worker | Cards |
|---|---|
| W1 | bookings-api, maintenance-scheduler, admin-console |
| W2 | notifications, customer-profiles, fleet-telemetry |
| W3 | pricing-engine, payments-gateway |

Every shared hub key has wanting services on at least two workers: timeout W1/W2, backup W2/W1, availability W2/W1. Rate limit is wanted only by W1 but traps payments-gateway (W3).

### Arms

| Arm | Rule | n |
|---|---|---|
| `first-wins` | First worker commit to touch a key wins; later conflicting changes to that key are dropped and logged. | 10 |
| `supervisor-merges` | Opus 5.5 sees each conflict (key, both values, both reasons) and writes the merged value. | 10 |
| `hub-owner` | Workers cannot edit `platform.yaml`; they send change requests, and the reducer (Opus 5.5) applies them. | 10 |
| `human-decides` | Terminal prompt per conflict: key, both values with one-line reasons, `[a]/[b]/[e]dit value`. Timer starts on render. Choice and seconds logged; the supervisor's pick for the same conflict is logged beside it. | 3 (5 if about 3 conflicts per run) |

### Partial failure

One worker fails deterministically (invalid payload after the one retry) in half the runs per arm (rounding toward failed); 1 of 3 in `human-decides`. Every arm ends with the same Opus merge report, told which workers returned nothing. Score: does that report name the unfixed services, or claim success silently?

### Scoring

The shared checker only (CAP-9): risks fixed of 12, regressions against numeric needs.

### Metrics per run

Textual conflicts (git merge), conflicts per hub key, resolution chosen per conflict, risks fixed (of 12), regressions, unfixed services named (failure runs), tokens (worker and reducer share), wall seconds, human seconds per conflict.

## Shared code

- `src/topologies/model.py`: add `Reply.served_model` from the SDK response or `claude -p` `modelUsage`.
- `src/topologies/guards.py` (new, used by 02 and 03 only): schema validation with one retry, token budget check, skipped-work reporting by name.
