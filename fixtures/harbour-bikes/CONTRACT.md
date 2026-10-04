# Rules for changing Harbour Bikes

Fixes are scored automatically by reading the files. A change outside these rules is recorded as "outside contract" and does not count as a fix, even if it would work.

1. **Files.** Change only files that already exist. Do not add, delete or rename files. Do not edit `libs/` or `teams.yaml`.
2. **Settings.** Platform defaults live in `platform.yaml`; a service changes its own values in its `service.yaml`:
   - `overrides:` holds only `http.default_timeout` and `http.default_rate_limit`.
   - Durations have a unit: `300ms`, `12s`. Rates are `20/s`, `600/min` or `none`.
   - `backup:` is `hourly/<N>d`, `daily/<N>d`, `weekly/<N>d` or `none`; `null` inherits `backup.policy`.
   - `instances:` is a whole number. `owner:` is `{team: <name>, contact: <email>}` for a team in `teams.yaml`.
3. **Python timeouts.** Keep the outbound call directly inside `charge()` or `send_email()`, as the only such call there, and pass `timeout=` a single number: a literal, a constant assigned once (in the module or the function), arithmetic with `+ - * /` on those, or `http_timeout("<service>")` imported at the top of the module with `from platform_config import http_timeout`.
   - No decorators on those functions, no recursion, no retry adapters (`HTTPAdapter`, `Retry`, `mount`), no changes to socket timeouts.
   - Retries, if any, are a `for attempt in range(<number>):` loop around the call inside the function.
   - The module must import without any environment variables set.
4. **Secrets.** No form of a key may remain in any file, including pieces of it. A secret comes from the environment either as `api_key: ${NAME}` in `config.yaml` (main.go expands it) or as `cfg.Processor.APIKey = os.Getenv("NAME")` in `main.go`, with the variable named by a string literal.
5. **Network.** Ingress rules stay in Terraform JSON (`network.tf.json`). A CIDR is a literal or `${var.<name>}` / `${local.<name>}` with a literal value in that file; no HCL, modules, prefix lists or other expressions.
6. **Dependencies.** Pin every package in the manifest itself: `name==version` in `requirements.txt`, an exact `x.y.z` in `package.json`. No `-r`, `-c` or other option lines.
7. **Code that reads platform values.** Leave the line in `pricing-engine/src/index.js` that reads `PLATFORM_HTTP_TIMEOUT`; change that service's timeout through `overrides:` or the hub.
