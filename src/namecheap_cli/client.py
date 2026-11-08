"""Small XML API client. Fixed HTTPS endpoints; no automatic mutation retries."""

import ipaddress
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from http.client import HTTPException

from . import __version__
from .config import Credentials, domain_name
from .errors import CliError
from .records import Host, Zone

ENDPOINTS = {
    "production": "https://api.namecheap.com/xml.response",
    "sandbox": "https://api.sandbox.namecheap.com/xml.response",
}
TIMEOUT = 30
MAX_RESPONSE = 4 * 1024 * 1024
HINTS = {
    "1011102": "Enable API access or check the API key and environment.",
    "1011150": "Whitelist this machine's public IPv4 in Namecheap API Access settings.",
    "2030288": "The domain uses another DNS provider. Edit records there.",
    "2016166": "The domain is not in this Namecheap account.",
    "2019166": "The domain is not in this Namecheap account.",
}


def local_name(node: ET.Element) -> str:
    return node.tag.rsplit("}", 1)[-1].lower()


def find_all(node: ET.Element, tag: str) -> list[ET.Element]:
    return [element for element in node.iter() if local_name(element) == tag.lower()]


def find(node: ET.Element, tag: str) -> ET.Element:
    elements = find_all(node, tag)
    if len(elements) != 1:
        raise CliError(
            "invalid_response", f"Expected one {tag} in the provider response.", exit_code=1
        )
    return elements[0]


def truth(value: str | None) -> bool:
    if value is None or value.lower() not in {"true", "false"}:
        raise CliError("invalid_response", "Missing or invalid provider boolean.", exit_code=1)
    return value.lower() == "true"


def number(value: str | None) -> int:
    try:
        if value is None:
            raise ValueError
        return int(value)
    except ValueError as exc:
        raise CliError(
            "invalid_response", "Missing or invalid provider integer.", exit_code=1
        ) from exc


def ipv4(value: str) -> str:
    try:
        return str(ipaddress.IPv4Address(value.strip()))
    except ValueError as exc:
        raise CliError(
            "ipv4_required", "Namecheap requires an IPv4 address. Set NAMECHEAP_CLIENT_IP."
        ) from exc


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CliError(
            "redirect_refused",
            "The API tried to redirect the request; no credential was forwarded.",
            exit_code=1,
        )


def detect_ipv4(profile: str) -> str:
    host = urllib.parse.urlsplit(ENDPOINTS[profile]).netloc
    request = urllib.request.Request(
        f"https://{host}/cdn-cgi/trace", headers={"User-Agent": f"namecheap-cli/{__version__}"}
    )
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=TIMEOUT) as response:
            trace = response.read(65536).decode("utf-8", "replace")
    except (OSError, HTTPException, CliError) as exc:
        raise CliError(
            "ip_detection_failed",
            "Cannot detect public IPv4. Set NAMECHEAP_CLIENT_IP.",
            exit_code=1,
            retryable=True,
        ) from exc
    for line in trace.splitlines():
        if line.startswith("ip="):
            return ipv4(line[3:])
    raise CliError(
        "ip_detection_failed", "No IP address returned. Set NAMECHEAP_CLIENT_IP.", exit_code=1
    )


class Client:
    def __init__(
        self,
        credentials: Credentials,
        client_ip: str,
        profile: str = "production",
        *,
        endpoint: str | None = None,
        allow_loopback: bool = False,
        interval: float = 1.25,
        opener: Callable | None = None,
    ) -> None:
        self.credentials = credentials
        self.client_ip = ipv4(client_ip)
        self.profile = profile
        self.endpoint = endpoint or ENDPOINTS[profile]
        parsed = urllib.parse.urlsplit(self.endpoint)
        is_local = (
            allow_loopback
            and parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            and not parsed.username
            and not parsed.password
        )
        if self.endpoint not in ENDPOINTS.values() and not is_local:
            raise CliError(
                "unsafe_endpoint", "Only the production and sandbox HTTPS endpoints are supported."
            )
        self._open = opener or urllib.request.build_opener(NoRedirect).open
        self._interval = interval
        self._last_call = float("-inf")

    def redact(self, value: str) -> str:
        return value.replace(self.credentials.api_key, "[REDACTED]")

    def call(self, command: str, params: dict[str, str] | None = None) -> ET.Element:
        params = params or {}
        auth = {
            "ApiUser": self.credentials.api_user,
            "ApiKey": self.credentials.api_key,
            "UserName": self.credentials.username,
            "ClientIp": self.client_ip,
            "Command": command,
        }
        if auth.keys() & params.keys():
            raise CliError(
                "invalid_request", "Parameters must not override authentication or command."
            )
        request = urllib.request.Request(
            self.endpoint,
            data=urllib.parse.urlencode({**auth, **params}).encode(),
            method="POST",
            headers={
                "User-Agent": f"namecheap-cli/{__version__}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        delay = self._interval - (time.monotonic() - self._last_call)
        if delay > 0:
            time.sleep(delay)
        self._last_call = time.monotonic()
        try:
            with self._open(request, timeout=TIMEOUT) as response:
                raw = response.read(MAX_RESPONSE + 1)
        except urllib.error.HTTPError as exc:
            raise CliError(
                "http_error",
                f"Namecheap returned HTTP {exc.code}.",
                exit_code=1,
                retryable=exc.code == 429 or exc.code >= 500,
            ) from None
        except (OSError, HTTPException) as exc:
            raise CliError(
                "network_error",
                "Could not complete the Namecheap request.",
                exit_code=1,
                details={"exception": type(exc).__name__},
                retryable=True,
            ) from None
        if len(raw) > MAX_RESPONSE or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
            raise CliError(
                "invalid_response",
                "Provider XML is oversized or contains a document type.",
                exit_code=1,
            )
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            # Never echo arbitrary HTTP response bodies; they can contain secrets.
            raise CliError(
                "invalid_response", "Namecheap returned a non-XML response.", exit_code=1
            ) from exc
        if local_name(root) != "apiresponse":
            raise CliError("invalid_response", "Unexpected XML response root.", exit_code=1)
        if (root.get("Status") or "").upper() != "OK":
            errors = [
                {
                    "number": e.get("Number", ""),
                    "message": self.redact(e.text or ""),
                    "hint": HINTS.get(e.get("Number", ""), ""),
                }
                for e in find_all(root, "error")
            ]
            raise CliError(
                "provider_error",
                "Namecheap rejected the request.",
                exit_code=1,
                details={"errors": errors},
            )
        result = find(root, "CommandResponse")
        if (result.get("Type") or "").lower() != command.lower():
            raise CliError(
                "invalid_response", "Response command does not match the request.", exit_code=1
            )
        return result

    @staticmethod
    def _params(domain: str) -> dict[str, str]:
        sld, tld = domain_name(domain).split(".", 1)
        return {"SLD": sld, "TLD": tld}

    @staticmethod
    def _domain(result: ET.Element, domain: str) -> None:
        if (result.get("Domain") or "").lower().rstrip(".") != domain:
            raise CliError(
                "invalid_response", "Response domain does not match the request.", exit_code=1
            )

    def domains(self) -> list[dict]:
        rows, seen = [], set()
        for page in range(1, 10001):
            response = self.call(
                "namecheap.domains.getList", {"PageSize": "100", "Page": str(page)}
            )
            batch = find_all(find(response, "DomainGetListResult"), "domain")
            for row in batch:
                name = domain_name(row.get("Name", ""))
                if name in seen:
                    raise CliError(
                        "invalid_response", "Domain pagination repeated a record.", exit_code=1
                    )
                seen.add(name)
                rows.append(
                    {
                        "name": name,
                        "expires": row.get("Expires", ""),
                        "auto_renew": truth(row.get("AutoRenew")),
                        "namecheap_dns": truth(row.get("IsOurDNS")),
                    }
                )
            total = number(find(response, "TotalItems").text)
            if len(rows) == total:
                return sorted(rows, key=lambda r: r["name"])
            if not batch or len(rows) > total:
                break
        raise CliError(
            "invalid_response", "Domain pagination was incomplete or inconsistent.", exit_code=1
        )

    def check(self, names: list[str]) -> list[dict]:
        result = []
        for start in range(0, len(names), 50):
            batch = names[start : start + 50]
            response = self.call("namecheap.domains.check", {"DomainList": ",".join(batch)})
            rows = find_all(response, "DomainCheckResult")
            if sorted(row.get("Domain", "").lower() for row in rows) != sorted(batch):
                raise CliError(
                    "invalid_response",
                    "Availability response omitted or duplicated a domain.",
                    exit_code=1,
                )
            for row in rows:
                error = row.get("ErrorNo", "0")
                result.append(
                    {
                        "name": row.get("Domain", "").lower(),
                        "available": truth(row.get("Available")) if error == "0" else None,
                        "premium": truth(row.get("IsPremiumName")) if error == "0" else None,
                        "premium_registration_price": row.get("PremiumRegistrationPrice"),
                        "error": None
                        if error == "0"
                        else {"number": error, "message": self.redact(row.get("Description", ""))},
                    }
                )
        if any(row["error"] for row in result):
            raise CliError(
                "availability_failed",
                "Some names could not be checked; null does not mean taken.",
                exit_code=1,
                details={"results": result},
            )
        return result

    def hosts(self, domain: str) -> Zone:
        response = self.call("namecheap.domains.dns.getHosts", self._params(domain))
        result = find(response, "DomainDNSGetHostsResult")
        self._domain(result, domain)
        if not truth(result.get("IsUsingOurDNS")):
            raise CliError(
                "external_dns", "Namecheap is not serving this domain's DNS records.", exit_code=3
            )
        hosts, unsupported = [], []
        known = {
            "HostId",
            "Name",
            "Type",
            "Address",
            "MXPref",
            "TTL",
            "AssociatedAppTitle",
            "FriendlyName",
            "IsActive",
            "IsDDNSEnabled",
        }
        for row in find_all(result, "host"):
            for key, value in row.attrib.items():
                unsafe = (
                    key not in known
                    or key in {"AssociatedAppTitle", "FriendlyName"}
                    and bool(value)
                    or key == "IsActive"
                    and value.lower() != "true"
                    or key == "IsDDNSEnabled"
                    and value.lower() != "false"
                )
                if unsafe:
                    unsupported.append(f"{row.get('Name', '?')}: {key}")
            hosts.append(
                Host(
                    row.get("Name", "").lower(),
                    row.get("Type", "").upper(),
                    row.get("Address", ""),
                    number(row.get("TTL")),
                    number(row.get("MXPref", "10")),
                )
            )
        email = result.get("EmailType")
        return Zone(
            domain, tuple(hosts), email.upper() if email else None, tuple(sorted(unsupported))
        )

    def set_hosts(self, domain: str, zone: Zone) -> None:
        params = {**self._params(domain), "EmailType": zone.email_type or ""}
        for index, host in enumerate(zone.hosts, 1):
            params.update(
                {
                    f"HostName{index}": host.name,
                    f"RecordType{index}": host.type,
                    f"Address{index}": host.address,
                    f"TTL{index}": str(host.ttl),
                }
            )
            if host.type == "MX":
                params[f"MXPref{index}"] = str(host.mxpref)
        self._confirm(
            self.call("namecheap.domains.dns.setHosts", params),
            domain,
            "DomainDNSSetHostsResult",
            "IsSuccess",
        )

    def nameservers(self, domain: str) -> dict:
        result = find(
            self.call("namecheap.domains.dns.getList", self._params(domain)),
            "DomainDNSGetListResult",
        )
        self._domain(result, domain)
        return {
            "domain": domain,
            "mode": "namecheap" if truth(result.get("IsUsingOurDNS")) else "custom",
            "nameservers": sorted(
                domain_name(e.text or "") for e in find_all(result, "nameserver")
            ),
        }

    def set_nameservers(self, domain: str, target: dict) -> None:
        default = target["mode"] == "namecheap"
        command = (
            "namecheap.domains.dns.setDefault" if default else "namecheap.domains.dns.setCustom"
        )
        params = self._params(domain)
        if not default:
            params["Nameservers"] = ",".join(target["nameservers"])
        self._confirm(
            self.call(command, params),
            domain,
            "DomainDNSSetDefaultResult" if default else "DomainDNSSetCustomResult",
            "Updated",
        )

    def _confirm(self, response: ET.Element, domain: str, tag: str, attribute: str) -> None:
        result = find(response, tag)
        self._domain(result, domain)
        if not truth(result.get(attribute)):
            raise CliError("write_rejected", "Namecheap did not confirm the change.", exit_code=1)
