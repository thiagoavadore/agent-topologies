"""Whether a dependency manifest pins every package to one exact version."""

import json
import re

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

# npm's exact version: optional "=" or "v", then MAJOR.MINOR.PATCH with optional prerelease and build metadata.
NPM_EXACT = re.compile(
    r"^[=v]?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?P<prerelease>-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
NPM_CARET = re.compile(r"^\^([1-9]\d*)\.(\d+)\.(\d+)$")
NPM_DEPENDENCY_FIELDS = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")


class Unpinned(Exception):
    """The manifest leaves at least one version open; the reason is the message."""


def pip_requirements(text: str) -> dict[str, Requirement]:
    """Parse requirements.txt lines into requirements by canonical name; option lines are refused."""
    requirements = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-"):
            raise Unpinned(f"line {number} is a pip option ({line.split()[0]}); pins must be in this file")
        try:
            requirement = Requirement(line)
        except InvalidRequirement as error:
            raise Unpinned(f"line {number} does not parse: {error}") from None
        name = canonicalize_name(requirement.name)
        if name in requirements:
            raise Unpinned(f"{requirement.name} is listed twice")
        requirements[name] = requirement
    return requirements


def pip_pinned_version(requirement: Requirement) -> Version:
    specifiers = list(requirement.specifier)
    if requirement.url or len(specifiers) != 1 or specifiers[0].operator not in ("==", "==="):
        raise Unpinned(f"{requirement.name} is not pinned to one version: {requirement}")
    try:
        return Version(specifiers[0].version)
    except InvalidVersion:
        raise Unpinned(f"{requirement.name} pin is not an exact version: {specifiers[0]}") from None


def check_requirements(text: str, original: str) -> None:
    """Raise Unpinned unless every line is `name==version`, every original package stays, and pins meet old ranges."""
    pinned = pip_requirements(text)
    for name, before in pip_requirements(original).items():
        if name not in pinned:
            raise Unpinned(f"{before.name} was removed instead of pinned")
    for name, requirement in pinned.items():
        version = pip_pinned_version(requirement)
        before = pip_requirements(original).get(name)
        # An open original range (e.g. firebase-admin>=6) must still hold; an old exact pin may be bumped.
        if before and before.specifier and not any(spec.operator in ("==", "===") for spec in before.specifier):
            if not SpecifierSet(str(before.specifier)).contains(version, prereleases=True):
                raise Unpinned(f"{requirement.name}=={version} is outside the original range {before.specifier}")


def npm_version(text: str) -> tuple[int, int, int]:
    match = NPM_EXACT.match(text)
    if not match:
        raise Unpinned(f"{text!r} is not an exact version")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def npm_original_allows(original: str, version: str) -> bool:
    """Whether `version` satisfies the original range; only the ranges the fixture uses are understood."""
    if original in ("latest", "*") or NPM_EXACT.match(original):
        return True
    caret = NPM_CARET.match(original)
    if not caret:
        raise ValueError(f"fixture uses an npm range this check does not understand: {original!r}")
    floor = tuple(int(part) for part in caret.groups())
    exact = npm_version(version)
    # npm ranges skip prereleases unless the range itself names one.
    return exact >= floor and exact[0] == floor[0] and not NPM_EXACT.match(version).group("prerelease")


def npm_dependencies(text: str) -> dict[str, str]:
    try:
        manifest = json.loads(text)
    except ValueError as error:
        raise Unpinned(f"package.json does not parse: {error}") from None
    if not isinstance(manifest, dict):
        raise Unpinned("package.json is not an object")
    dependencies = {}
    for field in NPM_DEPENDENCY_FIELDS:
        block = manifest.get(field, {})
        if not isinstance(block, dict):
            raise Unpinned(f"{field} is not an object")
        for name, spec in block.items():
            if not isinstance(spec, str):
                raise Unpinned(f"{name} version is not a string")
            dependencies[name] = spec
    return dependencies


def check_package_json(text: str, original: str) -> None:
    """Raise Unpinned unless every dependency is an exact version, every original one stays, and pins meet old ranges."""
    pinned = npm_dependencies(text)
    for name, before in npm_dependencies(original).items():
        if name not in pinned:
            raise Unpinned(f"{name} was removed instead of pinned")
        if not NPM_EXACT.match(pinned[name]):
            continue
        if not npm_original_allows(before, pinned[name]):
            raise Unpinned(f"{name}@{pinned[name]} is outside the original range {before}")
    for name, spec in pinned.items():
        if not NPM_EXACT.match(spec):
            raise Unpinned(f"{name} is {spec!r}, not an exact version")
