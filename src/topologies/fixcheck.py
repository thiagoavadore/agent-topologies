"""Decide, by parsing and running files, which planted risks a Harbour Bikes repo fixed and which needs it broke."""

import base64
import ipaddress
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

from topologies import pins
from topologies.harbour import FIXTURE, HUB_FILE, NEEDS, REGRESSION_NEEDS, RISKS, Need, planted_secret
from topologies.pytimeouts import CallSite, Unresolved, effective_timeout

# Value grammars for settings; a bare number has no unit and does not parse.
DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s)\s*$")
RATE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*/\s*(s|min)\s*$")
BACKUP = re.compile(r"^\s*(hourly|daily|weekly)\s*/\s*(\d+)\s*d\s*$")
BACKUP_PERIOD_DAYS = {"hourly": 1, "daily": 1, "weekly": 7}
ENV_PLACEHOLDER = re.compile(r"^\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))$")
SECRET_TOKEN = re.compile(rb"cp_live_[A-Za-z0-9]{8,}")
TF_REFERENCE = re.compile(r"^\$\{(var|local)\.([A-Za-z_][A-Za-z0-9_-]*)\}$")
PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(cidr) for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
)
HTTP_KEYS = ("http.default_timeout", "http.default_rate_limit")

BOOKINGS_CALL = CallSite("bookings-api", "bookings_api", "charge", ("post", "put", "request"), None, "requests")
NOTIFICATIONS_CALL = CallSite("notifications", "notifications", "send_email", ("SMTP", "SMTP_SSL"), 3, "smtp")


class NotFixed(Exception):
    """The risk is still open, or the value cannot be read; the reason is the message."""


@dataclass(frozen=True)
class RiskResult:
    fixed: bool
    reason: str


@dataclass(frozen=True)
class NeedResult:
    regressed: bool
    effective: str | None  # the raw setting the service ends up with, None if it cannot be read
    reason: str


@dataclass(frozen=True)
class CheckResult:
    risks: dict[str, RiskResult]
    needs: dict[str, NeedResult]

    @property
    def fixed(self) -> list[str]:
        return [risk_id for risk_id, result in self.risks.items() if result.fixed]

    @property
    def regressed(self) -> list[str]:
        return [need_id for need_id, result in self.needs.items() if result.regressed]


def parse_duration(value) -> float:
    """Seconds from a duration such as 300ms or 12s."""
    match = DURATION.match(value) if isinstance(value, str) else None
    if not match:
        raise NotFixed(f"{value!r} is not a duration with a unit (e.g. 300ms, 12s)")
    seconds = float(match.group(1)) / (1000 if match.group(2) == "ms" else 1)
    if seconds <= 0:
        raise NotFixed(f"{value!r} must be above zero")
    return seconds


def parse_rate(value) -> float | None:
    """Requests per second from a rate such as 20/s or 600/min; None for no limit."""
    if value is None or value == "none":
        return None
    match = RATE.match(value) if isinstance(value, str) else None
    if not match:
        raise NotFixed(f"{value!r} is not a rate (e.g. 20/s, 600/min, none)")
    per_second = float(match.group(1)) / (60 if match.group(2) == "min" else 1)
    if per_second <= 0:
        raise NotFixed(f"{value!r} must be above zero")
    return per_second


def parse_backup(value) -> tuple[str, int] | None:
    """(schedule, retention days) from a policy such as daily/30d; None for no backup."""
    if value is None or value == "none":
        return None
    match = BACKUP.match(value) if isinstance(value, str) else None
    if not match:
        raise NotFixed(f"{value!r} is not a backup policy (e.g. daily/30d, none)")
    schedule, days = match.group(1), int(match.group(2))
    if days < BACKUP_PERIOD_DAYS[schedule]:
        raise NotFixed(f"{value!r} keeps backups for less than one {schedule} period")
    return schedule, days


class Repo:
    def __init__(self, root: Path):
        self.root = root

    def text(self, relative: str) -> str:
        path = self.root / relative
        if not path.is_file():
            raise NotFixed(f"{relative} is missing")
        return path.read_text()

    def yaml(self, relative: str) -> dict:
        try:
            loaded = yaml.safe_load(self.text(relative))
        except yaml.YAMLError as error:
            raise NotFixed(f"{relative} does not parse: {error}") from None
        if not isinstance(loaded, dict):
            raise NotFixed(f"{relative} is not a mapping")
        return loaded

    def service(self, name: str) -> dict:
        return self.yaml(f"{name}/service.yaml")

    def hub(self, key: str):
        hub = self.yaml(HUB_FILE)
        if key not in hub:
            raise NotFixed(f"{HUB_FILE} has no {key}")
        return hub[key]

    def overrides(self, service: str) -> dict:
        overrides = self.service(service).get("overrides")
        if overrides is None:
            return {}
        if not isinstance(overrides, dict):
            raise NotFixed(f"{service} overrides is not a mapping")
        return overrides

    def http_setting(self, service: str, key: str) -> tuple[object, str]:
        """The service's value for an http.* hub key and where it came from: its override, else the hub."""
        overrides = self.overrides(service)
        if key in overrides:
            return overrides[key], f"{service} override"
        return self.hub(key), HUB_FILE

    def timeout_seconds(self, service: str) -> float:
        value, source = self.http_setting(service, "http.default_timeout")
        try:
            return parse_duration(value)
        except NotFixed as error:
            raise Unresolved(f"http.default_timeout from {source}: {error}") from None


def within(value: float, need: Need, parse: Callable, unit: str) -> str:
    """Raise NotFixed if `value` breaks the need's bounds; else describe the value."""
    if need.minimum is not None and value < parse(need.minimum):
        raise NotFixed(f"{value:g}{unit} is below {need.id} minimum {need.minimum}")
    if need.maximum is not None and value > parse(need.maximum):
        raise NotFixed(f"{value:g}{unit} is above {need.id} maximum {need.maximum}")
    return f"{value:g}{unit}, within {need.minimum or '-'}..{need.maximum or '-'}"


def timeout_fixed(repo: Repo, site: CallSite, need: Need) -> str:
    try:
        seconds = effective_timeout(repo.root, site, lambda: repo.timeout_seconds(site.service))
    except Unresolved as error:
        raise NotFixed(str(error)) from None
    if seconds is None:
        raise NotFixed(f"{site.function}() still has no timeout")
    if seconds <= 0:
        raise NotFixed(f"{site.function}() timeout {seconds:g} s is not above zero")
    return f"{site.function}() timeout " + within(seconds, need, parse_duration, " s")


def bookings_timeout(repo: Repo) -> str:
    return timeout_fixed(repo, BOOKINGS_CALL, NEEDS["bookings-api.payment_call_timeout"])


def notifications_timeout(repo: Repo) -> str:
    return timeout_fixed(repo, NOTIFICATIONS_CALL, NEEDS["notifications.smtp_timeout"])


def bookings_rate_limit(repo: Repo) -> str:
    value, source = repo.http_setting("bookings-api", "http.default_rate_limit")
    per_second = parse_rate(value)
    if per_second is None:
        raise NotFixed(f"public endpoints still have no rate limit ({source} says {value!r})")
    return f"rate limit from {source}: " + within(per_second, NEEDS["bookings-api.public_rate_limit_per_client"], parse_rate, "/s")


def go_code(text: str) -> str:
    """Go source with comments removed and string literals kept, so tokens in comments do not count."""
    out, i = [], 0
    while i < len(text):
        if text.startswith("//", i):
            i = text.find("\n", i) if "\n" in text[i:] else len(text)
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = len(text) if end == -1 else end + 2
        elif text[i] in "\"`'":
            quote, start = text[i], i
            i += 1
            while i < len(text) and text[i] != quote:
                i += 2 if text[i] == "\\" and quote != "`" else 1
            i += 1
            out.append(text[start:i])
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def secret_locations(root: Path, secret: str) -> list[str]:
    """Files under `root` holding the planted secret, its hex part, either base64-encoded, or any cp_live_ key."""
    tail = secret.removeprefix("cp_live_")
    needles = [secret, tail, base64.b64encode(secret.encode()).decode(), base64.b64encode(tail.encode()).decode()]
    found = []
    for path in sorted(root.rglob("*")):
        if ".git" in path.relative_to(root).parts or not path.is_file():
            continue
        content = path.read_bytes()
        if SECRET_TOKEN.search(content) or any(needle.encode() in content for needle in needles):
            found.append(str(path.relative_to(root)))
    return found


def payments_secret(repo: Repo) -> str:
    leaked = secret_locations(repo.root, planted_secret())
    if leaked:
        raise NotFixed(f"a card processor key is still in {', '.join(leaked)}")
    processor = repo.yaml("payments-gateway/config.yaml").get("processor")
    if not isinstance(processor, dict):
        raise NotFixed("payments-gateway/config.yaml has no processor mapping")
    main_go = go_code(repo.text("payments-gateway/main.go"))
    api_key = processor.get("api_key")
    if api_key is not None:
        match = ENV_PLACEHOLDER.match(api_key) if isinstance(api_key, str) else None
        if not match:
            raise NotFixed(f"processor.api_key is {api_key!r}, not an environment placeholder like ${{NAME}}")
        if "os.ExpandEnv(" not in main_go:
            raise NotFixed("main.go no longer expands environment placeholders in config.yaml")
        return f"api_key comes from ${match.group(1) or match.group(2)} through os.ExpandEnv"
    reads = re.findall(r'os\.(?:Getenv|LookupEnv)\("([A-Za-z_][A-Za-z0-9_]*)"\)', main_go)
    if not reads:
        raise NotFixed("api_key was removed but main.go reads no environment variable for it")
    return f"api_key removed from config; main.go reads {', '.join(reads)} from the environment"


def instances_fixed(repo: Repo, service: str) -> str:
    declared = repo.service(service).get("instances")
    if isinstance(declared, bool) or not isinstance(declared, int) or declared < 1:
        raise NotFixed(f"{service} instances is {declared!r}, not a whole number of at least 1")
    floor = repo.hub("availability.min_instances")
    if isinstance(floor, bool) or not isinstance(floor, int) or floor < 1:
        raise NotFixed(f"availability.min_instances is {floor!r}, not a whole number of at least 1")
    effective = max(declared, floor)
    if effective < 2:
        ignored = " (overrides are not read for availability; set instances)" if "availability.min_instances" in repo.overrides(service) else ""
        raise NotFixed(f"{service} still runs {effective} instance{ignored}")
    return f"{effective} instances (instances: {declared}, platform floor: {floor})"


def fleet_owner(repo: Repo) -> str:
    owner = repo.service("fleet-telemetry").get("owner")
    if not isinstance(owner, dict) or not isinstance(owner.get("team"), str) or not isinstance(owner.get("contact"), str):
        raise NotFixed(f"owner is {owner!r}, not a mapping with team and contact")
    teams = yaml.safe_load((FIXTURE / "teams.yaml").read_text())["teams"]
    team = next((entry for entry in teams if entry["name"].casefold() == owner["team"].strip().casefold()), None)
    if team is None:
        raise NotFixed(f"owner team {owner['team']!r} is not a team in teams.yaml")
    if owner["contact"].strip().casefold() != team["contact"].casefold():
        raise NotFixed(f"owner contact {owner['contact']!r} is not {team['name']}'s contact {team['contact']}")
    return f"owned by {team['name']}"


def backup_fixed(repo: Repo, service: str) -> str:
    own = repo.service(service).get("backup")
    if own is not None:
        policy, source = parse_backup(own), f"{service} backup"
    else:
        policy, source = parse_backup(repo.hub("backup.policy")), "platform backup.policy"
    if policy is None:
        ignored = " (overrides are not read for backup; set backup)" if "backup.policy" in repo.overrides(service) else ""
        raise NotFixed(f"{service} data still has no backup ({source} is none){ignored}")
    return f"{policy[0]} backups kept {policy[1]} days, from {source}"


def pricing_pins(repo: Repo) -> str:
    try:
        pins.check_package_json(repo.text("pricing-engine/package.json"), (FIXTURE / "pricing-engine/package.json").read_text())
    except pins.Unpinned as error:
        raise NotFixed(str(error)) from None
    return "every package.json dependency is an exact version"


def notifications_pins(repo: Repo) -> str:
    try:
        pins.check_requirements(repo.text("notifications/requirements.txt"), (FIXTURE / "notifications/requirements.txt").read_text())
    except pins.Unpinned as error:
        raise NotFixed(str(error)) from None
    return "every requirement is pinned with =="


class Terraform:
    """The Terraform JSON files of one directory, with var and local references resolved to literals."""

    ALLOWED_BLOCKS = {"variable", "locals", "resource", "output", "terraform", "provider", "data"}

    def __init__(self, directory: Path):
        if any(directory.rglob("*.tf")):
            raise NotFixed(f"{directory.name} has HCL .tf files, which this check does not parse")
        self.documents, self.variables, self.locals = [], {}, {}
        for path in sorted(directory.rglob("*.tf.json")):
            try:
                document = json.loads(path.read_text())
            except ValueError as error:
                raise NotFixed(f"{path.name} does not parse: {error}") from None
            if not isinstance(document, dict) or set(document) - self.ALLOWED_BLOCKS:
                raise NotFixed(f"{path.name} has blocks this check does not read: {sorted(set(document) - self.ALLOWED_BLOCKS)}")
            for name, variable in (document.get("variable") or {}).items():
                if name in self.variables:
                    raise NotFixed(f"variable {name} is declared twice")
                self.variables[name] = variable.get("default") if isinstance(variable, dict) else None
            for name, value in (document.get("locals") or {}).items():
                if name in self.locals:
                    raise NotFixed(f"local {name} is declared twice")
                self.locals[name] = value
            self.documents.append(document)

    def resolve(self, value, depth: int = 0):
        if depth > 10:
            raise NotFixed("Terraform references nest too deep")
        if isinstance(value, list):
            resolved = [self.resolve(item, depth + 1) for item in value]
            return [part for item in resolved for part in (item if isinstance(item, list) else [item])]
        if not isinstance(value, str) or "${" not in value:
            return value
        match = TF_REFERENCE.match(value)
        if not match:
            raise NotFixed(f"cannot resolve Terraform expression {value!r}")
        kind, name = match.groups()
        table = self.variables if kind == "var" else self.locals
        if table.get(name) is None:
            raise NotFixed(f"{kind}.{name} has no literal value")
        return self.resolve(table[name], depth + 1)

    def cidrs(self, body: dict, *keys: str) -> list:
        found = []
        for key in keys:
            value = self.resolve(body.get(key) or [])
            if not isinstance(value, list):
                raise NotFixed(f"{key} is {value!r}, not a list")
            found += value
        return found

    def resources(self, resource_type: str) -> list[tuple[str, dict]]:
        found = []
        for document in self.documents:
            for name, body in ((document.get("resource") or {}).get(resource_type) or {}).items():
                if not isinstance(body, dict):
                    raise NotFixed(f"{resource_type}.{name} is not an object")
                found.append((f"{resource_type}.{name}", body))
        return found


@dataclass(frozen=True)
class Ingress:
    where: str
    cidrs: list
    from_security_group: bool
    from_port: object
    to_port: object
    protocol: object


def ingress_rules(terraform: Terraform) -> list[Ingress]:
    rules = []
    for where, body in terraform.resources("aws_security_group_rule"):
        kind = terraform.resolve(body.get("type"))
        if kind not in ("ingress", "egress"):
            raise NotFixed(f"{where} type is {kind!r}")
        if kind == "egress":
            continue
        if body.get("prefix_list_ids"):
            raise NotFixed(f"{where} uses prefix lists, which this check cannot resolve")
        cidrs = terraform.cidrs(body, "cidr_blocks", "ipv6_cidr_blocks")
        from_group = bool(body.get("source_security_group_id") or body.get("self"))
        rules.append(Ingress(where, cidrs, from_group, *(terraform.resolve(body.get(k)) for k in ("from_port", "to_port", "protocol"))))
    for where, body in terraform.resources("aws_security_group"):
        for index, block in enumerate(body.get("ingress") or []):
            if not isinstance(block, dict) or block.get("prefix_list_ids"):
                raise NotFixed(f"{where} ingress {index} cannot be read")
            cidrs = terraform.cidrs(block, "cidr_blocks", "ipv6_cidr_blocks")
            from_group = bool(block.get("security_groups") or block.get("self"))
            rules.append(Ingress(f"{where}.ingress[{index}]", cidrs, from_group, *(terraform.resolve(block.get(k)) for k in ("from_port", "to_port", "protocol"))))
    for where, body in terraform.resources("aws_vpc_security_group_ingress_rule"):
        if body.get("prefix_list_id"):
            raise NotFixed(f"{where} uses a prefix list, which this check cannot resolve")
        cidrs = [terraform.resolve(body[k]) for k in ("cidr_ipv4", "cidr_ipv6") if body.get(k)]
        from_group = bool(body.get("referenced_security_group_id"))
        rules.append(Ingress(where, cidrs, from_group, *(terraform.resolve(body.get(k)) for k in ("from_port", "to_port", "ip_protocol"))))
    return rules


def private_cidr(cidr) -> bool:
    if not isinstance(cidr, str):
        raise NotFixed(f"CIDR {cidr!r} is not a string")
    try:
        network = ipaddress.ip_network(cidr, strict=True)
    except ValueError:
        raise NotFixed(f"{cidr!r} is not a valid CIDR block") from None
    return any(network.version == private.version and network.subnet_of(private) for private in PRIVATE_NETWORKS)


def reaches_https(rule: Ingress) -> bool:
    if str(rule.protocol).lower() in ("-1", "all"):
        return True
    ports = (rule.from_port, rule.to_port)
    if not all(isinstance(port, int) and not isinstance(port, bool) for port in ports):
        raise NotFixed(f"{rule.where} ports {ports!r} are not numbers")
    return str(rule.protocol).lower() in ("tcp", "6") and rule.from_port <= 443 <= rule.to_port


def admin_exposure(repo: Repo) -> str:
    directory = repo.root / "admin-console"
    terraform = Terraform(directory)
    rules = ingress_rules(terraform)
    for rule in rules:
        public = [cidr for cidr in rule.cidrs if not private_cidr(cidr)]
        if public:
            raise NotFixed(f"{rule.where} still admits {', '.join(public)}")
    staff_paths = [rule for rule in rules if (rule.cidrs or rule.from_security_group) and reaches_https(rule)]
    if not staff_paths:
        raise NotFixed("no ingress rule lets staff reach the console on 443 any more")
    return f"ingress only from private networks or security groups: {', '.join(rule.where for rule in staff_paths)}"


RISK_CHECKS: dict[str, Callable[[Repo], str]] = {
    "bookings-api.missing_timeout": bookings_timeout,
    "bookings-api.no_rate_limit": bookings_rate_limit,
    "payments-gateway.hardcoded_secret": payments_secret,
    "fleet-telemetry.single_point_of_failure": lambda repo: instances_fixed(repo, "fleet-telemetry"),
    "fleet-telemetry.no_owner": fleet_owner,
    "customer-profiles.missing_backup": lambda repo: backup_fixed(repo, "customer-profiles"),
    "pricing-engine.unpinned_dependency": pricing_pins,
    "admin-console.public_exposure": admin_exposure,
    "notifications.missing_timeout": notifications_timeout,
    "notifications.unpinned_dependency": notifications_pins,
    "maintenance-scheduler.single_point_of_failure": lambda repo: instances_fixed(repo, "maintenance-scheduler"),
    "maintenance-scheduler.missing_backup": lambda repo: backup_fixed(repo, "maintenance-scheduler"),
}
assert set(RISK_CHECKS) == {risk.id for risk in RISKS}

NEED_PARSERS = {"http.default_timeout": (parse_duration, " s"), "http.default_rate_limit": (parse_rate, "/s")}


def need_result(repo: Repo, need: Need) -> NeedResult:
    parse, unit = NEED_PARSERS[need.measures]
    try:
        value, source = repo.http_setting(need.service, need.measures)
    except NotFixed as error:
        return NeedResult(True, None, f"{need.measures} for {need.service} cannot be read: {error}")
    try:
        parsed = parse(value)
        if parsed is None:
            return NeedResult(False, str(value), f"no limit from {source}")
        return NeedResult(False, str(value), f"from {source}: " + within(parsed, need, parse, unit))
    except NotFixed as error:
        return NeedResult(True, str(value), f"from {source}: {error}")


def check(repo_path: Path) -> CheckResult:
    """Score a Harbour Bikes repo: each planted risk fixed or not, each regression need broken or not, with reasons."""
    repo = Repo(Path(repo_path))
    risks = {}
    for risk in RISKS:
        try:
            risks[risk.id] = RiskResult(True, RISK_CHECKS[risk.id](repo))
        except (NotFixed, OSError, UnicodeDecodeError) as error:
            risks[risk.id] = RiskResult(False, str(error))
        except Exception as error:  # malformed edits must score as not fixed, never crash a benchmark run
            risks[risk.id] = RiskResult(False, f"unreadable input ({type(error).__name__}: {error})")
    needs = {need_id: need_result(repo, NEEDS[need_id]) for need_id in REGRESSION_NEEDS}
    return CheckResult(risks, needs)
