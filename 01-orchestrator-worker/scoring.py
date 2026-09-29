"""Score a run against the risks planted in the fixtures, matched exactly on (file, category)."""

import json
from pathlib import Path

from orchestrator import CATEGORIES

PLANTED_PATH = Path(__file__).parent / "fixtures" / "planted.json"


def load_planted(path: Path = PLANTED_PATH) -> set[tuple[str, str]]:
    planted = {(item["file"], item["category"]) for item in json.loads(path.read_text())["planted"]}
    if not planted:
        raise ValueError(f"{path.name} has no planted risks")
    unknown = {category for _, category in planted} - set(CATEGORIES)
    if unknown:
        raise ValueError(f"planted.json uses categories the worker contract does not know: {sorted(unknown)}")
    missing = {file for file, _ in planted if not (path.parent / "services" / file).exists()}
    if missing:
        raise ValueError(f"planted.json names cards that don't exist: {sorted(missing)}")
    return planted


def score(findings: list[dict], planted: set[tuple[str, str]]) -> dict:
    found = {(finding["file"], finding["category"]) for finding in findings}
    return {
        "planted": len(planted),
        "planted_found": len(found & planted),
        "recall": round(len(found & planted) / len(planted), 3),
        "extra_findings": len(found - planted),
        "missed": sorted(f"{file}:{category}" for file, category in planted - found),
    }
