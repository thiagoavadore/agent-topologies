# How the checker scores a Harbour Bikes repo

`topologies.fixcheck.check(repo)` reads a copy of this repo after agents have edited it. For each of the 11 planted risks it returns fixed or not, with a reason. For each of the two planted needs it returns regressed or not, with the value the service ends up with. No model is involved. Every rule parses a file (Python AST, YAML, JSON, Terraform JSON, pip and npm version rules) or runs the code; none reads prose.

The answer key (risk IDs, needs, team registry, the original secret and manifests) comes from the pristine fixture in this repository, never from the repo under check, so editing a `needs:` block or `teams.yaml` cannot move the goalposts.

## Three outcomes per risk

- **Fixed**: the edit is inside the worker contract and the risk is closed.
- **Not fixed**: the edit is inside the contract and the risk is still open (or the code is broken: an undefined name, a run that fails, code that disagrees with its own run).
- **Not fixed, outside contract** (`RiskResult.outside_contract`, reason starts with `outside contract:`): the edit uses a form the contract excludes, so the checker does not judge it. Reported separately so a reader can tell "the agent left the risk open" from "the agent did something the scorer was told not to read".

Needs work the same way: `NeedResult.regressed`, plus `outside_contract` when the value is unreadable by contract (for example a worded duration) or the code stopped reading the platform value.

## The worker contract

[`CONTRACT.md`](CONTRACT.md) is the text workers get in their prompt (`topologies.harbour.worker_contract()`), and the checker enforces it:

| Contract rule | Enforced by |
|---|---|
| Change only existing files; no adds, deletes or renames; do not edit `libs/` or `teams.yaml` | File list and shared-file bytes compared with the pristine fixture (`harbour.contract_breaches`). A change in a service directory voids that service's risks; a change at the repo root voids all risks; a `libs/` edit voids the two timeout risks; a `teams.yaml` edit voids `no_owner`. Ignored: `.git`, `__pycache__`, `.DS_Store`, top-level `*.md` and `.gitignore`. |
| Settings formats, `overrides:` only for `http.*` keys, `instances` a whole number, `owner` a `{team, contact}` mapping | Value grammars below; anything else is outside contract |
| Python timeouts as literal, constant assigned once, `+ - * /`, or top-level-imported `http_timeout(<service>)`; one call directly in the function; no decorators, recursion, retry adapters or socket-timeout changes; retries only as `for _ in range(<number>)` | AST rules in `pytimeouts.py`. Parameters, imports, tuples, environment reads, `**kwargs`, other loops and the rest are outside contract. |
| Secret from `${NAME}` in config or `cfg.Processor.APIKey = os.Getenv("NAME")` | YAML plus comment-stripped Go; `os.Getenv(variable)` is outside contract |
| Terraform JSON only; CIDRs literal or `${var.x}`/`${local.x}` with a literal value | `.tf` HCL, unknown blocks, prefix lists and other expressions are outside contract |
| Pins in the manifest; no pip option lines | `-r`/`-c`/`--hash` and lines that are not requirements are outside contract |
| No new environment reads, except the secret's named variable | Python: every `os.environ[...]`, `os.environ.get`, `os.getenv` or other `os.environ` use in `bookings_api.py` / `notifications.py` is compared with the pristine module, before anything runs; a new or non-literal read is outside contract. Go: any `os.Getenv`/`os.LookupEnv` in `main.go` other than the one assigned to `APIKey` is outside contract |
| Keep pricing-engine's `PLATFORM_HTTP_TIMEOUT` line | Exact line present in `pricing-engine/src/index.js`, else the pricing need is regressed, outside contract |

## Settings: where a value comes from

| Setting | Effective value |
|---|---|
| `http.default_timeout`, `http.default_rate_limit` | The service's `overrides:` entry, else `platform.yaml` |
| Backup | The service's `backup:` field, else `platform.yaml` `backup.policy` (`null` inherits; `none` opts out) |
| Instances | The larger of the service's `instances:` and `platform.yaml` `availability.min_instances` |

Formats: durations need a unit (`300ms`, `12s`); rates are `N/s`, `N/min` or `none`; backup is `hourly|daily|weekly/<days>d` or `none`. Anything else is outside contract. Zero is readable but never meets a need.

## Planted risks

| Risk ID | Fixed when | How it is checked |
|---|---|---|
| `bookings-api.missing_timeout` | The payment call in `charge()` waits at least 12 s per attempt and at most 25 s across all attempts | AST resolves `timeout=` to one number and counts `range(N)` retries; then `charge()` runs in a child process (clean environment, network blocked, `requests` replaced by a recorder) and the timeout it passes must equal the AST value |
| `notifications.missing_timeout` | The `smtplib.SMTP`/`SMTP_SSL` call in `send_email()` waits at least 2 s per attempt and at most 20 s across attempts | Same, with `socket.create_connection` recorded. A test calibrates this against a real silent SMTP server: the client gives up after the recorded timeout |
| `bookings-api.no_rate_limit` | Effective `http.default_rate_limit` for bookings-api is 2/s to 50/s | YAML, rate grammar |
| `payments-gateway.hardcoded_secret` | No file outside `.git/objects` holds the planted key, its hex part, base64 of either, any `cp_live_` key, or (outside `.md` files) the bare `cp_live_` prefix; and the key comes from the environment | Byte scan; then either `processor.api_key` is exactly `${NAME}`/`$NAME` and comment-stripped `main.go` still calls `os.ExpandEnv(`, or `api_key` is absent or `""` and `main.go` assigns `.APIKey = os.Getenv("NAME")` |
| `fleet-telemetry.single_point_of_failure` | Effective instances is at least 2 | YAML |
| `fleet-telemetry.no_owner` | `owner:` names a team in the original `teams.yaml` with that team's contact | YAML |
| `customer-profiles.missing_backup` | Effective backup is a schedule whose retention covers at least one period (`weekly/1d` fails) | YAML, backup grammar |
| `maintenance-scheduler.missing_backup` | Same | Same |
| `pricing-engine.unpinned_dependency` | Every entry in `dependencies`, `devDependencies`, `optionalDependencies`, `peerDependencies` is one exact version; every original package stays; `demand-model-client` stays inside `^2.0.0` | JSON; npm exact-version grammar; no ranges, tags, aliases or URLs |
| `notifications.unpinned_dependency` | Every requirement is `name==version` with a real version; every original package stays; `firebase-admin` stays inside `>=6` | `packaging` |
| `admin-console.public_exposure` | Every ingress CIDR sits inside 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10 or fc00::/7, and some ingress rule still lets staff reach 443 (from a private CIDR or a security group) | JSON; `ipaddress` with strict CIDRs; covers `aws_security_group_rule`, inline `aws_security_group` ingress and `aws_vpc_security_group_ingress_rule` |

## Needs

Values live in each service's `service.yaml` (`needs:`) with the reason beside them.

| Need | Bound | Why | Role |
|---|---|---|---|
| `bookings-api.payment_call_timeout` | 12 s per attempt, 25 s in total | payments-gateway may take 4 s per attempt and retries twice; the app gives up at 30 s | Fix criterion |
| `notifications.smtp_timeout` | 2 s per attempt, 20 s in total | The relay takes up to 1.5 s to greet under load; the Celery soft limit is 30 s | Fix criterion |
| `bookings-api.public_rate_limit_per_client` | 2/s to 50/s | The app sends at most 1/s per customer and retries once; above 50/s is abuse | Fix criterion |
| `pricing-engine.demand_model_timeout` | at most 300 ms | bookings-api waits 400 ms for a price | **Regression**: met at the start through the hub's 300ms; a hub timeout raised for bookings-api or notifications breaks it |
| `payments-gateway.inbound_rate_per_caller` | at least 20/s, or no limit | bookings-api charges at up to 20/s at peak | **Regression**: met at the start (hub `none`); a global limit below 20/s, set to fix bookings-api, breaks it |

## Fake-fix suite

`tests/test_fixcheck.py` applies the full reference fix, breaks one thing in a way that looks like a fix, and asserts the risk is not fixed (or the need regressed) for the expected reason and with the expected contract flag. 114 fakes (105 fixes, 9 regressions, 50 of them outside contract); see `FAKE_FIXES`, `FAKE_REGRESSIONS`, `REJECTION_REASONS` and `OUTSIDE_CONTRACT` there. `ALTERNATIVE_FIXES` holds honest variants that must still count (literal constants, `import platform_config`, sessions, `SMTP_SSL`, a positional SMTP timeout, hub-level backup and floor, a bounded retry, `Getenv` with an empty `api_key`, a security-group source, the CGNAT VPN range, a `"//"` comment key, exception handling and extra headers around the call).

## Remaining limits

What the checker still cannot judge, so a reader can discount the numbers.

- **The contract narrows what counts.** An honest fix outside it (a helper function, a `(connect, read)` tuple, a parameter default, a `TimeoutHTTPAdapter`, a lockfile, a new file such as `terraform.tfvars.json` or a `.env.example`) scores "outside contract". Workers get the contract in their prompt; the outside-contract count is reported per run so its size is visible.
- **Environment reads are enforced where code is scored**: `bookings_api.py`, `notifications.py` and `main.go`. A new `process.env` read in pricing-engine's JavaScript, or one in the Rust, Kotlin or Ruby files, breaks the contract but is not detected.
- **Go is not compiled.** The env-read check finds `os.ExpandEnv(` or `.APIKey = os.Getenv("NAME")` in comment-stripped `main.go`; it does not prove the processor client uses that field.
- **Secret obfuscation.** A key reversed, hex-encoded, or split so that the `cp_live_` prefix itself is broken up would pass. The key also stays in git history; rotation is out of scope.
- **The regression needs are read from configuration.** pricing-engine's JavaScript is checked only for the one line that reads the platform value; payments-gateway's Go is not read at all, so a limiter added in code would not be seen.
- **Timeouts are per socket operation.** `send_email()` with 10 s can take longer across connect, STARTTLS, login and send; the need bounds each wait and the retry count, not the wall clock.
- **The `requests` recorder is a stand-in.** It shows the timeout the code passes, not that the real library honours it (it does; the SMTP path is calibrated against a real server, the `requests` path is not). Code that needs `requests` features the stand-in lacks fails its run and scores not fixed.
- **Running agent code is not a sandbox.** The child process has a clean environment, blocked network and a 20 s cap, but normal file and process access.
- **Owner.** Any existing team passes; whether Team Fleet is the right owner is not judged.
- **Single point of failure is an instance count.** Two fleet-telemetry instances in one zone or a broker with no standby pass. A hub `availability.min_instances` of 2 also gives maintenance-scheduler a second instance, splitting its local SQLite state; that service is no longer scored, so the harm is not counted.
- **Backup is a policy.** Restores are not tested.
- **Exposure is security-group CIDRs.** Load balancers, WAF and SSO are not considered, and a narrow public allowlist (an office IP) counts as not fixed by design.
- **Pins are syntax plus the original range.** Whether a version exists on the registry is not checked.
- **The bounds are the authors' choices.** The maxima (25 s, 20 s, 50/s) and the 2/s floor reject absurd values; they are pre-registered, not measured.

## Answer key changes before the benchmark

Changes made before the first benchmark run, each with its evidence. After the first run, any change needs a dated note in the folder README and a full re-run.

- **2026-10-05: `bookings-api.no_rate_limit` locator now points at `needs.public_rate_limit_per_client`** (commit 4ce7633), a key a reader can cite in the unchanged repo, instead of `overrides.http.default_rate_limit`, which does not exist until someone adds it. The rule itself did not change.
- **2026-10-05: `maintenance-scheduler.single_point_of_failure` removed; 11 planted risks remain.** Across 5 dry runs of 03, the worker owning maintenance-scheduler always declined to raise `instances` and gave the right reason: `plan.sqlite` and `history.sqlite` live on `vm-local-disk`, so a second instance splits state, and the contract offers no legal way to move that state first. A risk that cannot be fixed inside the contract measures the answer key, not the workers (the same lesson as folder 01's httpx key). The fixture keeps `instances: 1` as a realistic, unscored fact.
