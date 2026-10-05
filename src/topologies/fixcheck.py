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
from topologies.harbour import (
    FIXTURE,
    HUB_FILE,
    NEEDS,
    REGRESSION_NEEDS,
    RISKS,
    RISKS_BY_ID,
    Need,
    OutsideContract,
    contract_breaches,
    planted_secret,
)
from topologies.pytimeouts import CallSite, Unresolved, effective_timeout

# Value grammars for settings; a bare number has no unit and does not parse.
DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s)\s*$")
RATE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*/\s*(s|min)\s*$")
BACKUP = re.compile(r"^\s*(hourly|daily|weekly)\s*/\s*(\d+)\s*d\s*$")
BACKUP_PERIOD_DAYS = {"hourly": 1, "daily": 1, "weekly": 7}
ENV_PLACEHOLDER = re.compile(r"^\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))$")
SECRET_TOKEN = re.compile(rb"cp_live_[A-Za-z0-9]{8,}")
SECRET_PREFIX = b"cp_live_"
TF_REFERENCE = re.compile(r"^\$\{(var|local)\.([A-Za-z_][A-Za-z0-9_-]*)\}$")
PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(cidr) for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fc00::/7")
)
HTTP_KEYS = ("http.default_timeout", "http.default_rate_limit")
PRICING_TIMEOUT_LINE = "const timeoutMs = parseDuration(process.env.PLATFORM_HTTP_TIMEOUT);"

BOOKINGS_CALL = CallSite("bookings-api", "bookings_api", "charge", ("post", "put", "request"), None, "requests")
NOTIFICATIONS_CALL = CallSite("notifications", "notifications", "send_email", ("SMTP", "SMTP_SSL"), 3, "smtp")


class NotFixed(Exception):
    """The risk is still open, or the value cannot be read; the reason is the message."""


@dataclass(frozen=True)
class RiskResult:
    fixed: bool
    reason: str
    outside_contract: bool = False  # not fixed because the edit used a form the worker contract excludes


@dataclass(frozen=True)
class NeedResult:
    regressed: bool
    effective: str | None  # the raw setting the service ends up with, None if it cannot be read
    reason: str
    outside_contract: bool = False


@dataclass(frozen=True)
class CheckResult:
    risks: dict[str, RiskResult]
    needs: dict[str, NeedResult]

    @property
    def fixed(self) -> list[str]:
        return [risk_id for risk_id, result in self.risks.items() if result.fixed]

    @property
    def outside_contract(self) -> list[str]:
        return [risk_id for risk_id, result in self.risks.items() if result.outside_contract]

    @property
    def regressed(self) -> list[str]:
        return [need_id for need_id, result in self.needs.items() if result.regressed]


def parse_duration(value) -> float:
    """Seconds from a duration such as 300ms or 12s."""
    match = DURATION.match(value) if isinstance(value, str) else None
    if not match:
        raise OutsideContract(f"{value!r} is not a duration with a unit (e.g. 300ms, 12s)")
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
        raise OutsideContract(f"{value!r} is not a rate (e.g. 20/s, 600/min, none)")
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
        raise OutsideContract(f"{value!r} is not a backup policy (e.g. daily/30d, none)")
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
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise NotFixed(f"{relative} is not UTF-8") from None

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
            raise OutsideContract(f"{service} overrides is not a mapping")
        unknown = sorted(set(overrides) - set(HTTP_KEYS))
        if unknown:
            raise OutsideContract(f"{service} overrides holds {', '.join(map(str, unknown))}; only http.* keys may be overridden")
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
        except OutsideContract as error:
            raise OutsideContract(f"http.default_timeout from {source}: {error}") from None


def within(value: float, need: Need, parse: Callable, unit: str) -> str:
    """Raise NotFixed if `value` breaks the need's bounds; else describe the value."""
    if need.minimum is not None and value < parse(need.minimum):
        raise NotFixed(f"{value:g}{unit} is below {need.id} minimum {need.minimum}")
    if need.maximum is not None and value > parse(need.maximum):
        raise NotFixed(f"{value:g}{unit} is above {need.id} maximum {need.maximum}")
    return f"{value:g}{unit}, within {need.minimum or '-'}..{need.maximum or '-'}"


def timeout_fixed(repo: Repo, site: CallSite, need: Need) -> str:
    """Each attempt must wait at least the need's minimum; all attempts together at most its maximum."""
    try:
        timeout = effective_timeout(repo.root, site, lambda: repo.timeout_seconds(site.service))
    except Unresolved as error:
        raise NotFixed(str(error)) from None
    if timeout.seconds is None:
        raise NotFixed(f"{site.function}() still has no timeout")
    if timeout.seconds <= 0:
        raise NotFixed(f"{site.function}() timeout {timeout.seconds:g} s is not above zero")
    if need.minimum is not None and timeout.seconds < parse_duration(need.minimum):
        raise NotFixed(f"{timeout.seconds:g} s per attempt is below {need.id} minimum {need.minimum}")
    if need.maximum is not None and timeout.worst_case > parse_duration(need.maximum):
        raise NotFixed(f"{timeout.attempts} x {timeout.seconds:g} s is above {need.id} maximum {need.maximum}")
    return f"{site.function}() timeout {timeout.attempts} x {timeout.seconds:g} s, within {need.minimum}..{need.maximum}"


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
    """Files holding the planted secret, its hex part, base64 of either, any cp_live_ key, or the prefix outside docs."""
    tail = secret.removeprefix("cp_live_")
    needles = [secret, tail, base64.b64encode(secret.encode()).decode(), base64.b64encode(tail.encode()).decode()]
    found = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if relative.parts[:2] == (".git", "objects") or not path.is_file():
            continue
        content = path.read_bytes()
        split_key = SECRET_PREFIX in content and path.suffix != ".md"
        if split_key or SECRET_TOKEN.search(content) or any(needle.encode() in content for needle in needles):
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
    assigned = re.findall(r'\.APIKey\s*=\s*os\.Getenv\("([A-Za-z_][A-Za-z0-9_]*)"\)', main_go)
    if assigned and api_key in (None, ""):
        return f"main.go sets APIKey from {assigned[0]} in the environment"
    if re.search(r"\.APIKey\s*=\s*os\.(?:Getenv|LookupEnv)\(", main_go) and not assigned:
        raise OutsideContract('main.go reads the key from the environment, but not as os.Getenv("NAME") with a literal name')
    allowed = {f'"{name}"' for name in assigned[:1]}
    other_reads = [argument.strip() for argument in re.findall(r"os\.(?:Getenv|LookupEnv)\(([^)]*)\)", main_go) if argument.strip() not in allowed]
    if other_reads:
        raise OutsideContract(f"main.go reads environment variables other than the key: {', '.join(other_reads)}")
    if api_key not in (None, ""):
        match = ENV_PLACEHOLDER.match(api_key) if isinstance(api_key, str) else None
        if not match:
            raise NotFixed(f"processor.api_key is {api_key!r}, not an environment placeholder like ${{NAME}}")
        if "os.ExpandEnv(" not in main_go:
            raise NotFixed("main.go no longer expands environment placeholders in config.yaml")
        return f"api_key comes from ${match.group(1) or match.group(2)} through os.ExpandEnv"
    raise NotFixed("api_key is gone from config.yaml but main.go never sets APIKey from the environment")


def instances_fixed(repo: Repo, service: str) -> str:
    declared = repo.service(service).get("instances")
    if isinstance(declared, bool) or not isinstance(declared, int) or declared < 1:
        raise OutsideContract(f"{service} instances is {declared!r}, not a whole number of at least 1")
    floor = repo.hub("availability.min_instances")
    if isinstance(floor, bool) or not isinstance(floor, int) or floor < 1:
        raise OutsideContract(f"availability.min_instances is {floor!r}, not a whole number of at least 1")
    effective = max(declared, floor)
    if effective < 2:
        repo.overrides(service)
        raise NotFixed(f"{service} still runs {effective} instance")
    return f"{effective} instances (instances: {declared}, platform floor: {floor})"


def fleet_owner(repo: Repo) -> str:
    owner = repo.service("fleet-telemetry").get("owner")
    if not isinstance(owner, dict) or not isinstance(owner.get("team"), str) or not isinstance(owner.get("contact"), str):
        raise OutsideContract(f"owner is {owner!r}, not a mapping with team and contact")
    teams = yaml.safe_load((FIXTURE / "teams.yaml").read_text(encoding="utf-8"))["teams"]
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
        repo.overrides(service)
        raise NotFixed(f"{service} data still has no backup ({source} is none)")
    return f"{policy[0]} backups kept {policy[1]} days, from {source}"


def pricing_pins(repo: Repo) -> str:
    try:
        pins.check_package_json(repo.text("pricing-engine/package.json"), (FIXTURE / "pricing-engine/package.json").read_text(encoding="utf-8"))
    except pins.Unpinned as error:
        raise NotFixed(str(error)) from None
    return "every package.json dependency is an exact version"


def notifications_pins(repo: Repo) -> str:
    try:
        pins.check_requirements(repo.text("notifications/requirements.txt"), (FIXTURE / "notifications/requirements.txt").read_text(encoding="utf-8"))
    except pins.Unpinned as error:
        raise NotFixed(str(error)) from None
    return "every requirement is pinned with =="


class Terraform:
    """The Terraform JSON files of one directory, with var and local references resolved to literals."""

    ALLOWED_BLOCKS = {"//", "variable", "locals", "resource", "output", "terraform", "provider", "data"}

    def __init__(self, directory: Path):
        if any(directory.rglob("*.tf")):
            raise OutsideContract(f"{directory.name} has HCL .tf files; ingress rules stay in Terraform JSON")
        self.documents, self.variables, self.locals = [], {}, {}
        for path in sorted(directory.rglob("*.tf.json")):
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except ValueError as error:
                raise NotFixed(f"{path.name} does not parse: {error}") from None
            if not isinstance(document, dict) or set(document) - self.ALLOWED_BLOCKS:
                raise OutsideContract(f"{path.name} has blocks this check does not read: {sorted(set(document) - self.ALLOWED_BLOCKS)}")
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
            raise OutsideContract(f"Terraform expression {value!r} is not a literal, var or local")
        kind, name = match.groups()
        table = self.variables if kind == "var" else self.locals
        if table.get(name) is None:
            raise OutsideContract(f"{kind}.{name} has no literal value in the Terraform JSON")
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
            raise OutsideContract(f"{where} uses prefix lists")
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
            raise OutsideContract(f"{where} uses a prefix list")
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
    "maintenance-scheduler.missing_backup": lambda repo: backup_fixed(repo, "maintenance-scheduler"),
}
assert set(RISK_CHECKS) == {risk.id for risk in RISKS}

NEED_PARSERS = {"http.default_timeout": (parse_duration, " s"), "http.default_rate_limit": (parse_rate, "/s")}


def need_result(repo: Repo, need: Need) -> NeedResult:
    """Breaking the need, or making it unreadable, is a regression."""
    parse, unit = NEED_PARSERS[need.measures]
    try:
        value, source = repo.http_setting(need.service, need.measures)
    except OutsideContract as error:
        return NeedResult(True, None, f"outside contract: {error}", outside_contract=True)
    except Exception as error:
        return NeedResult(True, None, f"{need.measures} for {need.service} cannot be read: {error}")
    if need.service == "pricing-engine" and PRICING_TIMEOUT_LINE not in repo.text("pricing-engine/src/index.js"):
        return NeedResult(True, str(value), "outside contract: pricing-engine no longer reads PLATFORM_HTTP_TIMEOUT", outside_contract=True)
    try:
        parsed = parse(value)
        if parsed is None:
            return NeedResult(False, str(value), f"no limit from {source}")
        return NeedResult(False, str(value), f"from {source}: " + within(parsed, need, parse, unit))
    except OutsideContract as error:
        return NeedResult(True, str(value), f"outside contract: from {source}: {error}", outside_contract=True)
    except NotFixed as error:
        return NeedResult(True, str(value), f"from {source}: {error}")


def breach_for(risk_id: str, breaches: dict[str, str]) -> str | None:
    """The file-level contract breach that voids this risk's fix, if any."""
    service = RISKS_BY_ID[risk_id].service
    scopes = [service, "repo"]
    if risk_id in ("bookings-api.missing_timeout", "notifications.missing_timeout"):
        scopes.append("libs/platform_config.py")
    if risk_id == "fleet-telemetry.no_owner":
        scopes.append("teams.yaml")
    return next((breaches[scope] for scope in scopes if scope in breaches), None)


def check(repo_path: Path) -> CheckResult:
    """Score a Harbour Bikes repo: each planted risk fixed or not, each regression need broken or not, with reasons."""
    repo = Repo(Path(repo_path))
    breaches = contract_breaches(repo.root)
    risks = {}
    for risk in RISKS:
        try:
            breach = breach_for(risk.id, breaches)
            if breach:
                raise OutsideContract(breach)
            risks[risk.id] = RiskResult(True, RISK_CHECKS[risk.id](repo))
        except OutsideContract as error:
            risks[risk.id] = RiskResult(False, f"outside contract: {error}", outside_contract=True)
        except (NotFixed, OSError, UnicodeDecodeError) as error:
            risks[risk.id] = RiskResult(False, str(error))
        except Exception as error:  # malformed edits must score as not fixed, never crash a benchmark run
            risks[risk.id] = RiskResult(False, f"unreadable input ({type(error).__name__}: {error})")
    needs = {need_id: need_result(repo, NEEDS[need_id]) for need_id in REGRESSION_NEEDS}
    return CheckResult(risks, needs)
