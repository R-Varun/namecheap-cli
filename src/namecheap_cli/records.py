"""DNS values, deterministic snapshots, edits, and complete comparisons. No I/O."""

import hashlib
import ipaddress
import json
import re
from collections import Counter
from dataclasses import dataclass, replace
from typing import Any

from .config import domain_name
from .errors import CliError

TYPES = ("A", "AAAA", "ALIAS", "CNAME", "MX", "MXE", "NS", "TXT", "URL", "URL301", "FRAME")
EMAIL_TYPES = ("MX", "MXE", "FWD", "OX")
HOSTNAMES = frozenset({"ALIAS", "CNAME", "MX", "NS"})
REDIRECTS = frozenset({"URL", "URL301", "FRAME"})
ADDRESS_TYPES = frozenset({"A", "AAAA"})
ANSWERS = ADDRESS_TYPES | {"ALIAS"} | REDIRECTS
DEFAULT_TTL = 1800
DEFAULT_MXPREF = 10
_NAME = re.compile(r"^(?:\*|[a-z0-9_-]+)(?:\.[a-z0-9_-]+)*$")


def invalid(message: str) -> CliError:
    return CliError("invalid_record", message)


def fingerprint(data: Any, profile: str) -> str:
    raw = json.dumps({"profile": profile, "state": data}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def relative_name(host: str, domain: str) -> str:
    if not isinstance(host, str):
        raise invalid("The host name must be a string.")
    name = host.strip().lower().rstrip(".")
    if name in ("@", domain):
        return "@"
    if name.endswith("." + domain):
        name = name[: -(len(domain) + 1)]
    if not _NAME.fullmatch(name) or len(name + "." + domain) > 253:
        raise invalid(f"Invalid host name: {host!r}")
    if any(len(label) > 63 for label in name.split(".")):
        raise invalid("Host labels must be at most 63 characters.")
    return name


@dataclass(frozen=True)
class Host:
    name: str
    type: str
    address: str
    ttl: int = DEFAULT_TTL
    mxpref: int = DEFAULT_MXPREF

    @property
    def key(self) -> tuple:
        address = self.address
        if self.type in HOSTNAMES:
            address = address.lower().rstrip(".")
        elif self.type in ADDRESS_TYPES | {"MXE"}:
            try:
                address = str(ipaddress.ip_address(address))
            except ValueError:
                pass
        return self.name.lower(), self.type.upper(), address

    @property
    def full_key(self) -> tuple:
        return *self.key, self.ttl, self.mxpref if self.type == "MX" else 0

    def to_json(self) -> dict:
        data = {"name": self.name, "type": self.type, "address": self.address, "ttl": self.ttl}
        if self.type == "MX":
            data["mxpref"] = self.mxpref
        return data


def make_host(
    domain: str,
    name: str,
    rtype: str,
    address: str,
    ttl: int = DEFAULT_TTL,
    mxpref: int = DEFAULT_MXPREF,
) -> Host:
    if not isinstance(rtype, str) or not isinstance(address, str):
        raise invalid("Record type and address must be strings.")
    rtype = rtype.strip().upper()
    if rtype not in TYPES:
        raise invalid(f"Unsupported record type {rtype!r}; supported types: {', '.join(TYPES)}.")
    # TXT whitespace is DNS data, not formatting.
    value = address if rtype == "TXT" else address.strip()
    if not value or "\x00" in value:
        raise invalid("A record needs a nonempty value without NUL characters.")
    if type(ttl) is not int or not 60 <= ttl <= 60000:
        raise invalid("TTL must be an integer from 60 to 60000.")
    if type(mxpref) is not int or not 0 <= mxpref <= 65535:
        raise invalid("MX preference must be an integer from 0 to 65535.")
    if rtype in ADDRESS_TYPES | {"MXE"}:
        try:
            kind = ipaddress.IPv6Address if rtype == "AAAA" else ipaddress.IPv4Address
            value = str(kind(value))
        except ValueError as exc:
            raise invalid(
                f"{rtype} requires a valid {'IPv6' if rtype == 'AAAA' else 'IPv4'} address."
            ) from exc
    elif rtype in HOSTNAMES:
        value = domain_name(domain if value == "@" else value) + "."
    elif rtype in REDIRECTS:
        from urllib.parse import urlsplit

        try:
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError
        except ValueError as exc:
            raise invalid(
                "Redirect records require an http:// or https:// URL with a host."
            ) from exc
    return Host(relative_name(name, domain), rtype, value, ttl, mxpref)


@dataclass(frozen=True)
class Zone:
    domain: str
    hosts: tuple[Host, ...]
    email_type: str | None = None
    unsupported: tuple[str, ...] = ()

    def to_json(self) -> dict:
        return {
            "domain": self.domain,
            "email_type": self.email_type,
            "hosts": [host.to_json() for host in self.hosts],
        }

    def canonical(self) -> dict:
        return {
            "domain": self.domain,
            "email_type": self.email_type,
            "hosts": sorted(host.full_key for host in self.hosts),
            "unsupported": sorted(self.unsupported),
        }

    @classmethod
    def from_json(cls, domain: str, data: Any) -> "Zone":
        if not isinstance(data, dict) or set(data) - {"domain", "email_type", "hosts"}:
            raise invalid("A zone file must be an object with domain, email_type, and hosts.")
        if domain_name(data.get("domain", "")) != domain:
            raise invalid("The file domain does not match the command domain.")
        email = data.get("email_type")
        if email is not None and email not in EMAIL_TYPES:
            raise invalid("email_type must be MX, MXE, FWD, OX, or null (unknown).")
        if not isinstance(data.get("hosts"), list):
            raise invalid("hosts must be an array.")
        hosts = []
        for row in data["hosts"]:
            if (
                not isinstance(row, dict)
                or not {"name", "type", "address"} <= set(row)
                or set(row) - {"name", "type", "address", "ttl", "mxpref"}
            ):
                raise invalid("Each host needs name, type, address, and optional ttl/mxpref only.")
            hosts.append(
                make_host(
                    domain,
                    row["name"],
                    row["type"],
                    row["address"],
                    row.get("ttl", DEFAULT_TTL),
                    row.get("mxpref", DEFAULT_MXPREF),
                )
            )
        return cls(domain, tuple(hosts), email)


def validate(zone: Zone) -> None:
    if zone.unsupported:
        raise CliError(
            "unsupported_metadata",
            "This zone has provider fields the CLI cannot preserve.",
            exit_code=3,
            details={"fields": list(zone.unsupported)},
        )
    if not zone.hosts:
        raise invalid("Refusing an empty zone: setHosts would delete every record.")
    seen: set[tuple] = set()
    for host in zone.hosts:
        if host.type not in TYPES:
            raise CliError(
                "unsupported_record", f"Cannot safely rewrite record type {host.type}.", exit_code=3
            )
        make_host(zone.domain, host.name, host.type, host.address, host.ttl, host.mxpref)
        if host.key in seen:
            raise invalid(
                "Duplicate records with the same name, type, and address are not supported."
            )
        seen.add(host.key)
    for name in {host.name for host in zone.hosts}:
        types = [host.type for host in zone.hosts if host.name == name]
        if "CNAME" in types and (name == "@" or len(types) != 1):
            raise invalid(f"{name}: a CNAME must be the only record at a non-apex name.")
        single = [t for t in types if t in ANSWERS - ADDRESS_TYPES]
        if len(single) > 1 or (single and set(types) & ADDRESS_TYPES):
            raise invalid(
                f"{name}: address, alias, and redirect records conflict; use set to replace them."
            )


def edit(zone: Zone, action: str, host: Host, *, match_value: bool = True) -> Zone:
    if action == "add":
        matches = [h for h in zone.hosts if h.key == host.key]
        if matches:
            if matches[0].full_key == host.full_key:
                return zone
            raise invalid("This record exists with different attributes; use set or apply.")
        hosts = zone.hosts + (host,)
    elif action == "set":

        def conflicts(old: Host) -> bool:
            if old.name != host.name:
                return False
            return (
                old.type == host.type
                or "CNAME" in (old.type, host.type)
                or (
                    old.type in ANSWERS
                    and host.type in ANSWERS
                    and not {old.type, host.type} <= ADDRESS_TYPES
                )
            )

        hosts = tuple(h for h in zone.hosts if not conflicts(h)) + (host,)
    elif action == "remove":
        hosts = tuple(
            h
            for h in zone.hosts
            if not (
                h.name == host.name
                and h.type == host.type
                and (not match_value or h.key == host.key)
            )
        )
    else:
        raise invalid("Unknown record edit.")
    return replace(zone, hosts=hosts)


def settle_email(old: Zone, new: Zone, override: str | None) -> tuple[Zone, list[str]]:
    email = override if override is not None else old.email_type
    types = {host.type for host in new.hosts}
    if email not in EMAIL_TYPES:
        raise CliError(
            "email_mode_unknown",
            "The API did not return a supported email mode. "
            "Choose --email-type MX|MXE|FWD|OX explicitly after checking your mail settings.",
            exit_code=3,
        )
    if "MX" in types and "MXE" in types:
        raise invalid("MX and MXE records cannot coexist.")
    if email in {"MX", "MXE"} and email not in types:
        raise invalid(f"Email mode {email} needs a {email} record. Choose an explicit email mode.")
    if email in {"FWD", "OX"} and types & {"MX", "MXE"}:
        raise invalid(
            f"Email mode {email} conflicts with custom MX records. Set --email-type MX or MXE."
        )
    if email == "MX" and "MXE" in types or email == "MXE" and "MX" in types:
        raise invalid("The email mode does not match the mail records.")
    warnings = []
    if email != old.email_type:
        warnings.append(f"Email mode changes from {old.email_type or 'unknown'} to {email}.")
        if old.email_type == "FWD":
            warnings.append("Namecheap email forwarding will stop.")
    return replace(new, email_type=email), warnings


def differences(old: Zone, new: Zone) -> list[dict]:
    before, after = Counter(h.full_key for h in old.hosts), Counter(h.full_key for h in new.hosts)
    result = [
        {"action": "remove", "record": list(key)} for key in sorted((before - after).elements())
    ]
    result += [
        {"action": "add", "record": list(key)} for key in sorted((after - before).elements())
    ]
    if old.email_type != new.email_type:
        result.append({"action": "email", "before": old.email_type, "after": new.email_type})
    return result
