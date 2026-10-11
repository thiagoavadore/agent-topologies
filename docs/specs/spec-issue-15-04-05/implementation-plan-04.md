# Implementation plan: 04-hierarchical

For a coding agent with no other context. Read first: `SPEC.md` and `experiment-design.md` in this folder (the 04 half), then `03-fan-out-partial-reducer/` (`fanout.py`, `fanout_prompts.py`, `fanout_arms.py`, `fanout_hubfile.py`, `fanout_overrides.py`, `fanout_mandate.py`, `experiment.py`, `run.py`, `tests/fanout_helpers.py`, `README.md`). 04 copies 03's conventions: same client, same guards, same checker, same record and summary style.

## Ground rules

- Write only under `04-hierarchical/`. Do not edit `src/`, `fixtures/`, `tests/` (root), `01`/`02`/`03`, `05-event-bus/`, `pyproject.toml`, root `README.md` or `PREDICTIONS.md`. The coordinator has already added `04-hierarchical/tests` to `pyproject.toml` testpaths; if it is missing, run `uv run pytest 04-hierarchical/tests` and say so in your report, do not edit the file.
- Every model call goes through `topologies.model.Backend` and, for structured replies, `topologies.guards.call_and_validate`.
- Score only with `topologies.fixcheck.check()`. No folder-local fix rule.
- Tests make no model calls (`ScriptedBackend`).
- Baseline before you start: `uv run pytest` shows 305 passed. Report it.
- Do not commit.

## Files

### `hier_config.py`

```python
WORKER_MODEL = "claude-sonnet-5-5"
LEAD_MODEL = "claude-opus-5-5"
ARMS = ("flat", "by-team", "by-runtime", "by-runtime-registry")
CUTS = {
    "by-team": {
        "rentals-and-money": ["bookings-api", "pricing-engine", "payments-gateway"],
        "fleet": ["fleet-telemetry", "maintenance-scheduler"],
        "customer-and-back-office": ["notifications", "customer-profiles", "admin-console"],
    },
    "by-runtime": {
        "python": ["bookings-api", "notifications", "maintenance-scheduler"],
        "jvm-and-go": ["customer-profiles", "payments-gateway"],
        "node-ruby-rust": ["pricing-engine", "admin-console", "fleet-telemetry"],
    },
}
CUT_OF_ARM = {"by-team": "by-team", "by-runtime": "by-runtime", "by-runtime-registry": "by-runtime"}
REGISTRY_ARMS = ("by-runtime-registry",)
```

Plus `owner_team(service) -> str | None` read from the pristine `FIXTURE/<service>/service.yaml` (`owner.team`, None for fleet-telemetry), and `group_of(cut, service) -> str`. Comment the one non-obvious line: by-team groups whole teams along the booking call graph; fleet-telemetry is unowned and sits with Team Fleet.

### `hier_hub.py`

Copy, unchanged in behaviour, from 03 at 7888d48: `parse_hub`, `format_value`, `set_hub_values` (`fanout_hubfile.py`); `HTTP_KEYS`, `overrides_of`, `override_edits`, `strip_overrides`, `grant_override`, `service_overrides`, `effective_timeouts` (`fanout_overrides.py`); `MANDATED_FILES`, `mandate_violation` (`fanout_mandate.py`). Module docstring, one line: copied from 03 so this folder runs on its own (duplication logged as debt in the spec memlog). Add `scratch_copy(dest: Path) -> Path`: `shutil.copytree(FIXTURE, dest, ignore=shutil.ignore_patterns("CHECKER.md", "__pycache__", ".git"))`. No git in 04.

### `hier_harm.py`

```python
def harms(key: str, value: str) -> list[str]:
    """Regression needs (harbour.REGRESSION_NEEDS) that `value` for hub `key`, applied with no service override, would break."""
```
For each need in `REGRESSION_NEEDS` with `NEEDS[need].measures == key`: parse the value with `fixcheck.parse_duration` (timeout) or `fixcheck.parse_rate` (rate; `None` means no limit), compare to the need's `minimum`/`maximum` parsed the same way. A value that does not parse (`OutsideContract`, `NotFixed`) returns `[]`; the caller records `unreadable=True` via a second function `readable(key, value) -> bool`.

```python
def classify(key, value, requester_services: list[str]) -> dict:
    # {"harms": [{"need": need_id, "inside": bool}], "unreadable": bool}

def coupling_ladder(*, worker_requests, requests_up, regressed: list[str], tiered: bool) -> dict[str, str]:
    # one outcome per need in REGRESSION_NEEDS:
    # "not-threatened" | "absorbed-at-sub-lead" | "caught-at-top" | "reached-hub" | "introduced-by-top"
```
Rules, in order: regressed and some request up harms it → `reached-hub`; regressed and none does → `introduced-by-top`; a request up harms it → `caught-at-top`; a worker request harms it (tiered arms only) → `absorbed-at-sub-lead`; else `not-threatened`. In `flat` the worker requests are the requests up, so `absorbed-at-sub-lead` cannot occur.

### `hier_prompts.py`

Reuse 03's wording wherever the role is the same, so the reader can diff them.

- `REFERENCE_FILES = ("platform.yaml", "teams.yaml", "libs/platform_config.py")`.
- `WORKER_SYSTEM`: 03's `WORKER_SYSTEM` with `OVERRIDE_RULE_OWNED` replaced by "only the platform lead grants an override", `MANDATE_RULE` and `ADDENDUM_OWNED` as in 03, and the hub rule: "You cannot edit platform.yaml. Your hub_changes are requests to your lead, who decides what to send to the platform lead." First line says "You own the service listed in the prompt." Identical in every arm (workers run once per index).
- `worker_prompt(repo, service)`, `worker_schema(paths)`: as 03, for one service.
- `RULES_BLOCK`: the mandate line, the override rule and `worker_contract()`; given to every lead so no tier judges against rules it never saw.
- `SUB_LEAD_SYSTEM` (identical text for every cut; only the group is filled in):

  > You lead a group of services of a bike-rental platform: {services} (owner teams: {teams}). Each service has a worker who fixed its operational risks without seeing the other workers. Workers cannot edit platform.yaml; they sent you requests to change it. The platform lead owns platform.yaml, decides every value, and is the only one who can grant a service its own override. Send the platform lead the requests your group stands behind: you may forward, merge, change or drop your workers' requests, or add your own. Each request is one hub key, one value and a one-line reason.
  > The rules the workers followed: {rules}
  > Reply with JSON only, no prose, matching: {"requests": [{"key": "<hub key>", "value": "<value>", "reason": "<one line>"}]} (requests may be empty)

- `sub_lead_prompt(hub_text, services, worker_results)`: `platform.yaml`, then the full `service.yaml` of each service in the group, then each worker's requests (key, value, reason) and notes, labelled by service.
- `SUB_LEAD_SCHEMA`: `{"requests": [{"key": enum HUB_KEYS, "value": ONE_LINE or integer, "reason": ONE_LINE}]}`, `additionalProperties: false`, nothing else.
- `TOP_SYSTEM_FLAT`: 03's `HUB_OWNER_SYSTEM` with "Workers fixed services" kept and "You manage the eight service workers directly." added; prompt includes `needs_block()`.
- `TOP_SYSTEM_TIERED`: same text with "Your sub-leads each lead a group of services (listed); they sent the change requests." instead, and the sentence about needs reads "The needs of every service with one are listed" only when the registry is attached (two constants, or one template with a `{needs_sentence}` slot; keep every other word identical).
- `top_prompt(hub_text, requests_up, groups, needs: bool)`: `platform.yaml`, `RULES_BLOCK`, the groups (name and services; in `flat` the 8 services), `needs_block()` when `needs`, then each request as `- {from} ({services}) asks {key} = {value!r}. Reason: {reason}`.
- `TOP_SCHEMA`: copy of 03's `HUB_OWNER_SCHEMA` (hub decisions plus override grants).
- `needs_block()`: copy of 03's.

### `hier.py`

Dataclasses (all `asdict`-able):

- `WorkerResult`: service, status (`ok`, `no-change`, `invalid`, `error`), attempts, tokens, served_models, served_primary, files (changed only), hub_requests (list of dicts with key, value, reason), notes, mandate_rejections, override_edits, overrides_stripped, error, wall_seconds.
- `SubLeadResult`: group, services, requests, tokens, served_models, served_primary, fallback (`""` or `forwarded-unchanged`), error, prompt_chars.
- `TopResult`: decisions (key → {value, reason}), grants, tokens, served_models, served_primary, called (bool), error, prompt_chars.

Functions:

- `run_worker(service, *, backend, model, base: Path) -> WorkerResult`: call, then exactly 03's post-processing for mandated arms: drop unchanged files, strip overrides, revert mandate violations, normalise values with `str(...).strip()`. A backend error or invalid reply sets the status and leaves files empty.
- `run_workers(*, backend, model, base) -> list[WorkerResult]`: thread pool over `SERVICES`, results in `SERVICES` order.
- `run_sub_leads(cut, *, backend, model, hub_text, workers) -> list[SubLeadResult]`: one call per group, parallel. On error or `InvalidAfterRetry`: `requests` = the group's worker requests unchanged, `fallback="forwarded-unchanged"`, error recorded.
- `requests_up_of(arm, workers, sub_leads) -> list[dict]`: `flat`: one entry per worker request with `from=<service>`, `services=[service]`; tiered: one entry per sub-lead request with `from=<group>`, `services=<group services>`. Each entry gets `classify(...)`.
- `run_top(arm, *, backend, model, hub_text, requests_up, groups) -> TopResult`: not called when `requests_up` is empty. On error or invalid: no decisions, error recorded (the hub stays as it is).
- `resolve(arm, *, workers, sub_leads, top, hub_text, base_hub) -> dict`: fresh `scratch_copy`, write every worker's files, apply override grants with `grant_override`, set decided hub values that differ from base with `set_hub_values`, then `check()`, `effective_timeouts`, `service_overrides`, ladder. Returns the arm's measured fields.
- `run_pair(*, run: int, backend, backend_name, worker_model, lead_model, arms, parallel_arms=False) -> list[dict]`: workers once; sub-leads once per cut needed by `arms`; then per arm (in `rotated(arms, run - 1)` order) the top call and `resolve`. `by-runtime-registry` reuses the `by-runtime` sub-lead results; if only the registry arm is requested, the by-runtime sub-leads still run once.

One JSONL row per arm:

```
at, run, arm, backend, worker_model, lead_model, code_commit,
workers (list, identical across the rows of a run), sub_leads (list; [] in flat), sub_leads_shared_with ("by-runtime" in the registry row, else ""),
worker_requests (each with harms), requests_up (each with harms), top (TopResult),
final_hub, overrides_granted, overrides_in_merged, effective_timeouts, distinct_timeouts,
escalations, harmful_up_inside, harmful_up_outside, harmful_worker_requests, ladder,
score (03's score_record), tokens_by_tier {workers, sub_leads, top}, management_tokens, total_tokens,
top_prompt_chars, worker_served_primary, lead_served_primary, wall_seconds_workers, wall_seconds_management
```

`score_record` and `code_commit`: copy from 03's `fanout.py`.

### `run.py`

One run index for all arms (or `--arms ...`), printed per arm: requests up with harm flags, decisions, grants, fixed, regressions, ladder, tokens. Flags as 03's `run.py` (`--backend`, `--worker-model`, `--lead-model`, `--out`, `--json`, `--arms`). Refuses `--out` equal to `results/runs.jsonl`.

### `experiment.py`

- `DRAFT_FILE = ROOT / "docs/specs/spec-issue-15-04-05/predictions-draft.md"`, `PREDICTIONS = ROOT / "PREDICTIONS.md"`, `HEADING = "## 04-hierarchical:"`.
- `refuse_unregistered()`: `SystemExit` when the draft file is missing, contains `"DRAFT"`, or `PREDICTIONS.md` lacks `HEADING`. Called before any run; `--summarise-only` skips it.
- `run_pairs(n, arms, out, backend_name, worker_model, lead_model, parallel_arms, token_ceiling)`: for each index call `run_pair`, append rows, print one line per arm; `may_start_group` for the optional ceiling; `describe_skipped` at the end.
- `summarise(path) -> str`, refusing mixed setups (worker model, lead model, backend, served primaries), three tables:
  1. per arm: n; risks fixed mean (min); runs with a regression, and per need; outside contract mean; escalations mean; harmful requests up mean (inside / outside); overrides granted mean; distinct timeouts mean; tokens mean (workers / sub-leads / top), management tokens mean; top prompt chars mean; management wall mean.
  2. coupling ladder: rows = arm × need, columns = the five outcomes, cells = count of runs.
  3. per run: harmful worker requests (shared across arms) and, per arm, final `http.default_timeout` and `http.default_rate_limit` and regressed needs.
  Footer: models, served models, backend, run dates.
- Flags: `--n` (10), `--arms`, `--parallel-arms`, `--token-ceiling`, `--out`, `--summary`, `--backend`, `--worker-model`, `--lead-model`, `--summarise-only`.

### `README.md` (skeleton)

Headings as 03: `# 04 · Hierarchical: leads managing leads`; The question; The short answer (`TBD after the benchmark`); The experiment (tiers, the two cuts with the team table, the registry arm, the paired design, the mandate inherited from 03, the harm and ladder metrics, scoring by the shared checker); Results (`TBD`); Predictions (link to `PREDICTIONS.md`, hit/miss table `TBD`); Limits (one task, n=10, Claude only, harness applies edits, the six disclosed choices from `experiment-design.md`, paired workers, the flat lead's needs view, tokens are counts); Run it yourself (commands below); What's in the code (one row per file).

## Tests (`04-hierarchical/tests/`)

`hier_helpers.py`: copy the `_isolate()` pattern from `03-fan-out-partial-reducer/tests/fanout_helpers.py` with `MODULES = ("experiment", "run", "hier", "hier_config", "hier_hub", "hier_harm", "hier_prompts")`; a `Script` that routes by system prompt (worker / sub-lead / top lead) and by the service or group named in the prompt; reference fixes from `tests/harbour_fixes.py` (`FIXES`, `copy_fixture`).

| Test | Asserts |
|---|---|
| `test_cuts_cover_every_service_once` | Both cuts partition `SERVICES`; group sizes are {3, 2, 3} in both |
| `test_by_team_splits_no_owner_team` | Every owned service's team appears in one group only |
| `test_by_runtime_splits_rentals_fleet_and_bookings_calls` | bookings-api is in a different group from pricing-engine and from payments-gateway; maintenance-scheduler and fleet-telemetry are apart |
| `test_harms` | `http.default_timeout=15s` harms pricing; `300ms` harms nothing; `http.default_rate_limit=10/s` harms payments; `30/s` and `none` harm nothing; `fifteen seconds` is unreadable and harms nothing |
| `test_classify_inside_outside` | bookings-api's 15s request is outside in `flat` and in `by-runtime` (python), inside in `by-team` (rentals-and-money) |
| `test_ladder_outcomes` | One case per outcome; `flat` never yields `absorbed-at-sub-lead` |
| `test_worker_mandate_and_override_strip` | A literal timeout in `charge()` is reverted and logged; an `overrides:` edit is stripped; other edits kept |
| `test_workers_run_once_per_pair` | Script call counter: 8 worker calls for a 4-arm pair; worker files identical in all four rows |
| `test_registry_reuses_by_runtime_sub_leads` | 6 sub-lead calls for a 4-arm pair; the registry row's `sub_leads` equal the by-runtime row's |
| `test_sub_lead_sees_only_its_group` | The python sub-lead prompt contains bookings-api's and notifications' `service.yaml` and not pricing-engine's |
| `test_top_needs_only_in_flat_and_registry` | `needs_block()` text appears in the flat and registry top prompts, not in by-team or by-runtime |
| `test_top_not_called_without_requests` | No worker requests: no top call, `top.called` False, hub unchanged |
| `test_sub_lead_invalid_forwards_unchanged` | An invalid sub-lead reply twice: its workers' requests go up, `fallback="forwarded-unchanged"` |
| `test_checker_answer_key_not_in_scratch` | No `CHECKER.md` in any scratch copy |
| `test_end_to_end_cut_decides_regression` | The deterministic proof of the folder's value. Workers return the reference fixes (the bookings-api reference fixes write `overrides:`, which the harness strips: intended, the timeout and rate limit then hang on the hub or a grant) with bookings-api asking `http.default_timeout=15s` and `http.default_rate_limit=20/s`, notifications asking `5s`. Scripted leads: rentals-and-money drops the raise with a reason naming pricing's 300 ms; customer-and-back-office forwards `5s`; python forwards `15s`; the scripted top lead keeps 300 ms and grants overrides whenever a request reason mentions pricing or needs are in its prompt, else applies the highest timeout. Expect: `flat`, `by-team`, `by-runtime-registry` 0 regressions with bookings-api and notifications timeouts fixed; `by-runtime` regresses `pricing-engine.demand_model_timeout` with ladder `reached-hub`; `by-team` pricing ladder `caught-at-top`. |
| `test_experiment_refuses_draft` | Missing draft, draft containing DRAFT, or `PREDICTIONS.md` without the heading: `SystemExit`; all present and clean: passes (use `tmp_path` and monkeypatched paths) |
| `test_arm_order_rotates` | `rotated` shifts by index |
| `test_summary_refuses_mixed_setups` and `test_summary_renders` | On hand-built rows |
| `test_run_refuses_benchmark_file` | `run.py --out results/runs.jsonl` exits |

## Definition of done

1. `uv run pytest` passes: the 305 existing tests plus the new ones, no model calls. Quote the final line.
2. `git diff --stat` shows changes only under `04-hierarchical/`.
3. `uv run 04-hierarchical/run.py --out ~/.cache/issue15/04-scratch.jsonl` completes one real paired run (18 calls or fewer, about 5 min) and prints four arm blocks; every served model matches the requested one. Quote the per-arm summary lines.
4. `uv run 04-hierarchical/experiment.py --n 1` exits with the DRAFT refusal. Quote it.
5. README skeleton present with every section above.

## Commands for the README

```bash
uv sync
uv run pytest                                                            # no model calls
uv run 04-hierarchical/run.py --out ~/.cache/issue15/04-scratch.jsonl    # one paired run, all four arms
uv run 04-hierarchical/run.py --arms by-team by-runtime --out ~/.cache/issue15/04-scratch.jsonl
uv run 04-hierarchical/experiment.py                                     # n=10, results/runs.jsonl
uv run 04-hierarchical/experiment.py --parallel-arms                     # same, arms in parallel
uv run 04-hierarchical/experiment.py --summarise-only
```
