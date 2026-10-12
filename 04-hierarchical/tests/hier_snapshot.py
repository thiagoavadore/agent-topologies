"""Render every prompt of the `full` lead contract from fixed inputs, so a hash can pin them to the frozen benchmark's wording."""

import hashlib
import tempfile
from pathlib import Path

import hier_helpers  # noqa: F401  (puts this folder at the front of sys.path)
import hier_prompts
from hier import WorkerResult
from hier_config import CUTS, owner_team
from hier_hub import parse_hub, scratch_copy

REQUESTS_UP = [
    {"from": "python", "services": CUTS["by-runtime"]["python"], "key": "http.default_timeout", "value": "15s", "reason": "charges take up to 12 s"},
    {"from": "jvm-and-go", "services": CUTS["by-runtime"]["jvm-and-go"], "key": "http.default_rate_limit", "value": "50/s", "reason": "keep it high"},
]


def sub_lead_system_text(services: list[str], **kwargs) -> str:
    teams = sorted({owner_team(service) or "none" for service in services})
    return hier_prompts.sub_lead_system(services, teams, **kwargs)


def render(**kwargs) -> dict[str, str]:
    """Every prompt, keyed by name. `kwargs` pass the lead contract to the lead prompts."""
    with tempfile.TemporaryDirectory() as scratch:
        base = scratch_copy(Path(scratch) / "base")
        hub_text = (base / "platform.yaml").read_text()
        assert parse_hub(hub_text)
        worker = WorkerResult(service="bookings-api", status="ok", hub_requests=[{"key": "http.default_timeout", "value": "15s", "reason": "r"}], notes="n")
        out = {
            "worker_system": hier_prompts.worker_system(),
            "worker_prompt": hier_prompts.worker_prompt(base, "bookings-api"),
            "sub_lead_prompt": hier_prompts.sub_lead_prompt(hub_text, base, CUTS["by-runtime"]["python"], [worker]),
            "sub_lead_system_python": sub_lead_system_text(CUTS["by-runtime"]["python"], **kwargs),
            "sub_lead_system_rentals": sub_lead_system_text(CUTS["by-team"]["rentals-and-money"], **kwargs),
            "top_prompt_flat": hier_prompts.top_prompt(hub_text, REQUESTS_UP, {}, needs=True, **kwargs),
        }
        for cut in CUTS:
            for needs in (False, True):
                out[f"top_prompt_{cut}_needs_{needs}"] = hier_prompts.top_prompt(hub_text, REQUESTS_UP, CUTS[cut], needs=needs, **kwargs)
        for tiered in (False, True):
            for needs in (False, True):
                out[f"top_system_tiered_{tiered}_needs_{needs}"] = hier_prompts.top_system(tiered=tiered, needs=needs)
    return out


def digests(**kwargs) -> dict[str, str]:
    return {name: hashlib.sha256(text.encode()).hexdigest()[:16] for name, text in render(**kwargs).items()}
