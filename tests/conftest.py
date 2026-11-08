"""Synthetic API transport. This is a test double, not live-provider evidence."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl
from xml.sax.saxutils import escape, quoteattr

import pytest

from namecheap_cli.client import Client
from namecheap_cli.config import Config, Credentials
from namecheap_cli.records import Host, Zone
from namecheap_cli.service import Service
from namecheap_cli.storage import atomic_json

KEY = "synthetic-test-key-not-a-credential"
DOMAIN = "example.com"


def xml(command, inner, *, status="OK"):
    return (
        f'<ApiResponse xmlns="http://api.namecheap.com/xml.response" Status="{status}">'
        f"<CommandResponse Type={quoteattr(command)}>{inner}</CommandResponse></ApiResponse>"
    )


class FakeAPI:
    def __init__(self):
        self.zone = Zone(
            DOMAIN,
            (
                Host("@", "URL", "https://www.example.com/"),
                Host("www", "CNAME", "parking.example.net."),
            ),
            "FWD",
        )
        self.ns = {
            "domain": DOMAIN,
            "mode": "namecheap",
            "nameservers": ["dns1.example.net", "dns2.example.net"],
        }
        self.requests = []
        self.hook = lambda form: None
        self.corrupt = lambda zone: zone
        self.host_attrs = {}
        self.check_error = False
        self.domain_count = 2
        self.guard = threading.Lock()
        api = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                form = dict(parse_qsl(raw.decode(), keep_blank_values=True))
                with api.guard:
                    api.requests.append((self.path, form))
                override = api.hook(form)
                if override is not None:
                    body = override
                else:
                    body = api.respond(form)
                raw_response = body.encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw_response)))
                self.end_headers()
                self.wfile.write(raw_response)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/xml.response"
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True
        )

    def respond(self, form):
        command = form["Command"]
        if form.get("ApiKey") != KEY:
            return (
                '<ApiResponse Status="ERROR"><Errors>'
                '<Error Number="1011102">Invalid key</Error></Errors></ApiResponse>'
            )
        if command == "namecheap.domains.getList":
            names = [DOMAIN, "other.example"] + [
                f"d{i}.example" for i in range(max(0, self.domain_count - 2))
            ]
            page = int(form["Page"])
            batch = names[(page - 1) * 100 : page * 100]
            rows = "".join(
                f'<Domain Name="{name}" Expires="2027-01-01" AutoRenew="false" IsOurDNS="true" />'
                for name in batch
            )
            return xml(
                command,
                f"<DomainGetListResult>{rows}</DomainGetListResult>"
                f"<Paging><TotalItems>{len(names)}</TotalItems></Paging>",
            )
        if command == "namecheap.domains.check":
            rows = "".join(
                f'<DomainCheckResult Domain="{name}" Available="true" IsPremiumName="false" '
                f'ErrorNo="{2011170 if self.check_error else 0}" '
                'Description="synthetic domain error" />'
                for name in form["DomainList"].split(",")
            )
            return xml(command, rows)
        if command == "namecheap.domains.dns.getHosts":
            hosts = "".join(
                "<host "
                + " ".join(
                    f"{k}={quoteattr(str(v))}"
                    for k, v in {
                        "Name": host.name,
                        "Type": host.type,
                        "Address": host.address,
                        "TTL": host.ttl,
                        "MXPref": host.mxpref,
                        **self.host_attrs,
                    }.items()
                )
                + "/>"
                for host in self.zone.hosts
            )
            email = (
                ""
                if self.zone.email_type is None
                else f"EmailType={quoteattr(self.zone.email_type)}"
            )
            return xml(
                command,
                f'<DomainDNSGetHostsResult Domain="{DOMAIN}" IsUsingOurDNS="true" {email}>'
                f"{hosts}</DomainDNSGetHostsResult>",
            )
        if command == "namecheap.domains.dns.setHosts":
            hosts = []
            for index in range(1, 1000):
                if f"HostName{index}" not in form:
                    break
                hosts.append(
                    Host(
                        form[f"HostName{index}"],
                        form[f"RecordType{index}"],
                        form[f"Address{index}"],
                        int(form[f"TTL{index}"]),
                        int(form.get(f"MXPref{index}", "10")),
                    )
                )
            self.zone = self.corrupt(Zone(DOMAIN, tuple(hosts), form["EmailType"]))
            return xml(command, f'<DomainDNSSetHostsResult Domain="{DOMAIN}" IsSuccess="true" />')
        if command == "namecheap.domains.dns.getList":
            servers = "".join(
                f"<Nameserver>{escape(s)}</Nameserver>" for s in self.ns["nameservers"]
            )
            return xml(
                command,
                f'<DomainDNSGetListResult Domain="{DOMAIN}" '
                f'IsUsingOurDNS="{str(self.ns["mode"] == "namecheap").lower()}">'
                f"{servers}</DomainDNSGetListResult>",
            )
        if command in {"namecheap.domains.dns.setDefault", "namecheap.domains.dns.setCustom"}:
            default = command.endswith("setDefault")
            self.ns = {
                "domain": DOMAIN,
                "mode": "namecheap" if default else "custom",
                "nameservers": ["dns1.example.net", "dns2.example.net"]
                if default
                else sorted(form["Nameservers"].split(",")),
            }
            tag = "DomainDNSSetDefaultResult" if default else "DomainDNSSetCustomResult"
            return xml(command, f'<{tag} Domain="{DOMAIN}" Updated="true" />')
        raise AssertionError(command)

    @property
    def writes(self):
        return [form for _, form in self.requests if ".set" in form["Command"]]


@pytest.fixture
def api():
    fake = FakeAPI()
    fake.thread.start()
    try:
        yield fake
    finally:
        fake.server.shutdown()
        fake.server.server_close()


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "config" / "config.json"
    result = Config(path, (DOMAIN,))
    atomic_json(path, result.to_json())
    return result


@pytest.fixture
def factory(api):
    return lambda credentials, address, profile="production": Client(
        credentials, address, profile, endpoint=api.url, allow_loopback=True, interval=0
    )


@pytest.fixture
def service(factory, config, tmp_path):
    return Service(
        config, factory(Credentials("tester", KEY, "tester"), "127.0.0.1"), tmp_path / "state"
    )


@pytest.fixture
def env(config, tmp_path):
    return {
        "NAMECHEAP_API_USER": "tester",
        "NAMECHEAP_API_KEY": KEY,
        "NAMECHEAP_CLIENT_IP": "127.0.0.1",
        "NAMECHEAP_CONFIG_FILE": str(config.path),
        "NAMECHEAP_STATE_DIR": str(tmp_path / "state"),
    }
