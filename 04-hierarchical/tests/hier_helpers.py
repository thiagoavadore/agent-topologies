"""Import-safe setup and scripted model replies for the folder-04 tests.

01, 02 and 03 also have modules named `experiment` and `run`, so importing this module first puts this folder at
the front of `sys.path` and drops any same-named module cached from another folder.
"""

import json
import re
import sys
import tempfile
from pathlib import Path

import pytest

FOLDER = Path(__file__).resolve().parents[1]
ROOT = FOLDER.parent
MODULES = ("experiment", "run", "hier", "hier_config", "hier_hub", "hier_harm", "hier_prompts")


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

from harbour_fixes import copy_fixture, replace  # noqa: E402
from hier_config import CUTS  # noqa: E402

from topologies.harbour import FIXTURE, world_files  # noqa: E402

PRICING_REASON = "pricing-engine needs at most 300 ms"


def changed_files(service: str, apply) -> dict[str, str]:
    """Apply `apply(repo)` to a scratch copy of the fixture and return the new text of each changed file of `service`."""
    with tempfile.TemporaryDirectory() as scratch:
        repo = copy_fixture(Path(scratch) / "repo")
        apply(repo)
        return {
            path: (repo / path).read_text()
            for path in sorted(world_files(repo))
            if path.split("/")[0] == service and (repo / path).read_text() != (FIXTURE / path).read_text()
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


def payload(files: dict[str, str] | None = None, hub: list[tuple[str, str, str]] = (), notes: str = "") -> dict:
    return {
        "files": [{"path": path, "content": text} for path, text in (files or {}).items()],
        "hub_changes": [{"key": key, "value": value, "reason": reason} for key, value, reason in hub],
        "notes": notes,
    }


def asking_workers() -> dict[str, dict]:
    """bookings-api asks 15s and 20/s, notifications asks 5s; both read the hub from code."""
    return {
        "bookings-api": payload(
            changed_files("bookings-api", bookings_code_only),
            [("http.default_timeout", "15s", "charges take up to 12 s"), ("http.default_rate_limit", "20/s", "public clients need a limit")],
        ),
        "notifications": payload(changed_files("notifications", notifications_code_only), [("http.default_timeout", "5s", "the SMTP relay is slow")]),
    }


def forward(*requests: tuple[str, str, str]) -> str:
    return json.dumps({"requests": [{"key": key, "value": value, "reason": reason} for key, value, reason in requests]})


def protective_top(prompt: str) -> str:
    """Keeps 300ms and gives bookings-api and notifications their own timeouts when it can see pricing's constraint, else applies the highest timeout."""
    reasons = re.findall(r"Reason: (.*)", prompt.split("Change requests:")[1])
    sees_pricing = "Service needs (" in prompt or any("pricing" in reason for reason in reasons)
    requested = re.findall(r"asks http\.default_timeout = '(\d+)s'\. Reason", prompt)
    if sees_pricing:
        grants = [
            {"service": "bookings-api", "key": "http.default_timeout", "value": "15s", "reason": "charges take up to 12 s"},
            {"service": "notifications", "key": "http.default_timeout", "value": "5s", "reason": "the SMTP relay is slow"},
        ]
        return json.dumps({"hub": [{"key": "http.default_timeout", "value": "300ms", "reason": "pricing-engine needs 300ms"}], "overrides": grants})
    highest = max((int(seconds) for seconds in requested), default=None)
    hub = [{"key": "http.default_timeout", "value": f"{highest}s", "reason": "highest requested"}] if highest else []
    return json.dumps({"hub": hub, "overrides": []})


class Script:
    """Scripted replies for every role. `workers` maps service to a payload, raw text or exception; `sub_leads` maps group likewise."""

    def __init__(self, workers: dict[str, object] | None = None, sub_leads: dict[str, object] | None = None, top=None) -> None:
        self.workers, self.sub_leads, self.top = workers or {}, sub_leads or {}, top or '{"hub": []}'
        self.prompts: list[tuple[str, str, str, str]] = []  # (role, key, system, prompt): key is the service, the group or ""

    def __call__(self, system: str, prompt: str) -> str:
        if "You fix operational risks" in system:
            role, key = "worker", prompt.splitlines()[0].removeprefix("Your service: ")
            reply = self.workers.get(key, payload())
        elif "You lead a group of services" in system:
            services = system.split("platform: ", 1)[1].split(" (owner teams", 1)[0]
            role, key = "sub-lead", next(group for cut in CUTS.values() for group, found in cut.items() if ", ".join(found) == services)
            reply = self.sub_leads.get(key, forward())
        elif "You own platform.yaml" in system:
            role, key = "top", ""
            reply = self.top(prompt) if callable(self.top) else self.top
        else:
            raise AssertionError(f"unrecognised system prompt: {system[:80]!r}")
        self.prompts.append((role, key, system, prompt))
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(reply)

    def calls(self, role: str) -> list[tuple[str, str, str]]:
        return [(key, system, prompt) for found, key, system, prompt in self.prompts if found == role]


@pytest.fixture(autouse=True)
def stable_commit(monkeypatch):
    """Rows record the code commit, which is dirty while the code is uncommitted; tests pin it."""
    import hier

    monkeypatch.setattr(hier, "code_commit", lambda: "abc123")
