"""Import-safe setup and scripted model replies for the folder-03 tests.

01 and 02 also have modules named `experiment` and `run`, so importing this module first puts this folder at
the front of `sys.path` and drops any same-named module cached from another folder.
"""

import json
import sys
import tempfile
from pathlib import Path

FOLDER = Path(__file__).resolve().parents[1]
ROOT = FOLDER.parent
MODULES = ("experiment", "run", "fanout", "fanout_arms", "fanout_config", "fanout_hubfile", "fanout_prompts", "fanout_repo")


def _isolate() -> None:
    for name in MODULES:
        cached = sys.modules.get(name)
        if cached is not None and Path(getattr(cached, "__file__", "") or "").resolve().parent != FOLDER:
            del sys.modules[name]
    for path in (str(ROOT / "tests"), str(FOLDER)):
        if path in sys.path:
            sys.path.remove(path)
        sys.path.insert(0, path)


_isolate()

from fanout_config import WORKERS  # noqa: E402
from harbour_fixes import FIXES, copy_fixture, replace  # noqa: E402

from topologies.harbour import FIXTURE, RISKS_BY_ID, world_files  # noqa: E402

W1, W2, W3 = WORKERS


def changed_files(services: list[str], apply) -> dict[str, str]:
    """Apply `apply(repo)` to a scratch copy of the fixture and return the new text of each changed file in `services`."""
    with tempfile.TemporaryDirectory() as scratch:
        repo = copy_fixture(Path(scratch) / "repo")
        apply(repo)
        files = {}
        for path in sorted(world_files(repo)):
            if path.split("/")[0] in services and (repo / path).read_text() != (FIXTURE / path).read_text():
                files[path] = (repo / path).read_text()
        return files


def fixes_for(worker: str, risk_ids: list[str] | None = None) -> dict[str, str]:
    """Full new files of the reference fix for `worker`'s risks (all of them by default)."""
    ids = risk_ids if risk_ids is not None else [rid for rid, risk in RISKS_BY_ID.items() if risk.service in WORKERS[worker]]
    return changed_files(WORKERS[worker], lambda repo: [FIXES[rid](repo) for rid in ids])


def payload(files: dict[str, str] | None = None, hub: list[tuple[str, str, str]] = (), notes: str = "") -> dict:
    return {
        "files": [{"path": path, "content": text} for path, text in (files or {}).items()],
        "hub_changes": [{"key": key, "value": value, "reason": reason} for key, value, reason in hub],
        "notes": notes,
    }


def bookings_code_only(repo: Path) -> None:
    """The code half of the bookings-api timeout fix: it reads the platform timeout, no service override."""
    replace(repo, "bookings-api/bookings_api.py", "import requests\n", "import requests\nfrom platform_config import http_timeout\n")
    replace(
        repo,
        "bookings-api/bookings_api.py",
        'requests.post(PAYMENTS_URL + "/charge", json=booking.to_payment())',
        'requests.post(PAYMENTS_URL + "/charge", json=booking.to_payment(), timeout=http_timeout("bookings-api"))',
    )


def notifications_code_only(repo: Path) -> None:
    replace(repo, "notifications/notifications.py", "import smtplib\n", "import smtplib\n\nfrom platform_config import http_timeout\n")
    replace(repo, "notifications/notifications.py", "smtplib.SMTP(SMTP_HOST, 587)", 'smtplib.SMTP(SMTP_HOST, 587, timeout=http_timeout("notifications"))')


def colliding_payloads() -> dict[str, dict]:
    """W1 and W2 want different platform timeouts; W2 and W1 both want a backup policy; W3 changes nothing."""
    w1 = changed_files(WORKERS[W1], bookings_code_only)
    w2 = changed_files(WORKERS[W2], notifications_code_only)
    return {
        W1: payload(w1, [("http.default_timeout", "15s", "charges take up to 12 s")]),
        W2: payload(w2, [("http.default_timeout", "10s", "the SMTP relay is slow"), ("backup.policy", "daily/30d", "profiles has no backup")]),
        W3: payload(),
    }


class Script:
    """Scripted replies for every role. `workers[w]` is a payload dict, raw reply text, or an exception to raise."""

    def __init__(self, workers: dict[str, object], supervisor: str | None = None, hub_owner: str | None = None, report: str = "Merge report: nothing to add.") -> None:
        self.workers, self.supervisor, self.hub_owner, self.report = workers, supervisor, hub_owner, report
        self.prompts: list[tuple[str, str, str]] = []  # (role, system, prompt)

    def role_of(self, system: str, prompt: str) -> str:
        if "You own the merge of a shared platform file" in system:
            return "supervisor"
        if "You own platform.yaml" in system:
            return "hub-owner"
        if "merge report" in system:
            return "report"
        return "worker"

    def __call__(self, system: str, prompt: str) -> str:
        role = self.role_of(system, prompt)
        self.prompts.append((role, system, prompt))
        if role == "worker":
            first = prompt.splitlines()[0].removeprefix("Your services: ").split(", ")
            worker = next(name for name, services in WORKERS.items() if services == first)
            reply = self.workers[worker]
            if isinstance(reply, Exception):
                raise reply
            return reply if isinstance(reply, str) else json.dumps(reply)
        if role == "supervisor":
            return self.supervisor or '{"value": "300ms", "reason": "pricing-engine needs 300ms"}'
        if role == "hub-owner":
            return self.hub_owner or '{"hub": []}'
        return self.report

    def prompts_for(self, role: str) -> list[str]:
        return [prompt for found, _, prompt in self.prompts if found == role]
