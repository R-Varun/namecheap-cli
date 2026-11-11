import io
import urllib.error
from dataclasses import replace
from http.client import RemoteDisconnected
from unittest.mock import Mock

import pytest
from conftest import DOMAIN, KEY, xml

from namecheap_cli.client import MAX_RESPONSE, Client, NoRedirect, detect_ipv4, ipv4
from namecheap_cli.config import Credentials
from namecheap_cli.errors import CliError
from namecheap_cli.records import Host


def make_client(raw=None, *, exception=None):
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = raw
    opener = Mock(side_effect=exception, return_value=response)
    return Client(
        Credentials("tester", KEY, "tester"), "127.0.0.1", opener=opener, interval=0
    ), opener


def test_post_body_contains_credentials_but_url_does_not(factory):
    api = factory(Credentials("tester", KEY, "tester"), "127.0.0.1")
    assert api.hosts(DOMAIN).domain == DOMAIN


def test_request_shape_and_auth_override():
    api, opener = make_client(xml("test", "<Result />").encode())
    api.call("test")
    request = opener.call_args.args[0]
    assert request.method == "POST" and KEY.encode() in request.data and KEY not in request.full_url
    with pytest.raises(CliError) as error:
        api.call("test", {"ApiKey": "override"})  # pragma: allowlist secret
    assert error.value.code == "invalid_request"


@pytest.mark.parametrize(
    "exception", [TimeoutError(), urllib.error.URLError("offline"), RemoteDisconnected()]
)
def test_transport_failures_are_structured(exception):
    api, opener = make_client(exception=exception)
    with pytest.raises(CliError) as error:
        api.call("test")
    assert error.value.code == "network_error" and error.value.retryable
    assert opener.call_count == 1


@pytest.mark.parametrize("status,retryable", [(400, False), (429, True), (503, True)])
def test_http_failures(status, retryable):
    exc = urllib.error.HTTPError("https://api.namecheap.com", status, "error", {}, io.BytesIO())
    api, _ = make_client(exception=exc)
    with pytest.raises(CliError) as error:
        api.call("test")
    assert error.value.code == "http_error" and error.value.retryable == retryable


@pytest.mark.parametrize(
    "raw",
    [
        b"not XML",
        b"<Other />",
        b'<ApiResponse Status="OK"/>',
        b"<!DOCTYPE x><ApiResponse/>",
        b'<!ENTITY x "y">',
        b"x" * (MAX_RESPONSE + 1),
        xml("wrong", "<Result/>").encode(),
    ],
)
def test_bad_responses_do_not_echo_body(raw):
    api, _ = make_client(raw)
    with pytest.raises(CliError) as error:
        api.call("test")
    assert error.value.exit_code == 1


def test_api_error_redacts_key_and_adds_hint():
    raw = (
        '<ApiResponse Status="ERROR"><Errors><Error Number="1011102">'
        f"ApiKey={KEY}</Error></Errors></ApiResponse>"
    )
    api, _ = make_client(raw.encode())
    with pytest.raises(CliError) as error:
        api.call("test")
    details = error.value.details["errors"][0]
    assert KEY not in str(error.value.to_json()) and "[REDACTED]" in details["message"]
    assert "API" in details["hint"]


def test_namespace_and_host_normalization(factory):
    api = factory(Credentials("tester", KEY, "tester"), "127.0.0.1")
    assert len(api.hosts(DOMAIN).hosts) == 2


def test_wrong_domain_refused():
    api, _ = make_client(
        xml(
            "namecheap.domains.dns.getHosts",
            '<DomainDNSGetHostsResult Domain="wrong.example" IsUsingOurDNS="true"/>',
        ).encode()
    )
    with pytest.raises(CliError) as error:
        api.hosts(DOMAIN)
    assert error.value.code == "invalid_response"


def test_pagination(api, factory):
    api.domain_count = 205
    client = factory(Credentials("tester", KEY, "tester"), "127.0.0.1")
    assert len(client.domains()) == 205
    assert [form["Page"] for _, form in api.requests] == ["1", "2", "3"]


def test_pagination_repetition_refused(api, factory):
    api.domain_count = 105

    def hook(form):
        if form.get("Page") == "2":
            return api.respond({**form, "Page": "1"})

    api.hook = hook
    with pytest.raises(CliError) as error:
        factory(Credentials("tester", KEY, "tester"), "127.0.0.1").domains()
    assert error.value.code == "invalid_response"


def test_check_batches_and_does_not_call_failed_names_taken(api, factory):
    client = factory(Credentials("tester", KEY, "tester"), "127.0.0.1")
    assert len(client.check([f"d{i}.example" for i in range(105)])) == 105
    assert [len(form["DomainList"].split(",")) for _, form in api.requests] == [50, 50, 5]
    api.check_error = True
    with pytest.raises(CliError) as error:
        client.check([DOMAIN])
    assert error.value.code == "availability_failed"
    assert error.value.details["results"][0]["available"] is None


def test_write_params_include_all_records_and_mail_mode(api, service):
    api.zone = replace(
        api.zone, hosts=(Host("@", "MX", "mx.example.net.", mxpref=5),), email_type="MX"
    )
    plan = service.dns_edit(DOMAIN, "add", rtype="TXT", value=" keep exact ")
    service.dns_edit(
        DOMAIN, "add", rtype="TXT", value=" keep exact ", yes=True, if_match=plan["state_hash"]
    )
    form = api.writes[0]
    assert form["EmailType"] == "MX" and form["MXPref1"] == "5"
    assert form["Address2"] == " keep exact "
    assert all(path == "/xml.response" for path, _ in api.requests)


def test_endpoints_and_redirects_refused():
    creds = Credentials("tester", KEY, "tester")
    for endpoint in (
        "http://api.namecheap.com/xml.response",
        "https://evil.example",
        "http://127.0.0.1:1234",
    ):
        with pytest.raises(CliError):
            Client(creds, "127.0.0.1", endpoint=endpoint)
    with pytest.raises(CliError) as error:
        NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.example")
    assert error.value.code == "redirect_refused"


def test_public_ip_detection(monkeypatch):
    response = Mock()
    response.read.return_value = b"fl=123\nip=192.0.2.10\n"
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    monkeypatch.setattr("urllib.request.OpenerDirector.open", lambda *a, **kw: response)
    assert detect_ipv4("production") == "192.0.2.10"
    response.read.return_value = b"no ip"
    with pytest.raises(CliError):
        detect_ipv4("production")
    with pytest.raises(CliError):
        ipv4("2001:db8::1")


def test_rate_limit_spacing(monkeypatch):
    api, _ = make_client(xml("test", "<Result/>").encode())
    api._interval = 1.25
    clock = iter([10.0, 10.0, 10.1, 11.25])
    monkeypatch.setattr("time.monotonic", lambda: next(clock))
    sleep = Mock()
    monkeypatch.setattr("time.sleep", sleep)
    api.call("test")
    api.call("test")
    assert sleep.call_args.args[0] == pytest.approx(1.15)


@pytest.mark.parametrize(
    "domain,sld,tld",
    [
        ("example.com", "example", "com"),
        ("example.co.uk", "example", "co.uk"),
        ("example.com.au", "example", "com.au"),
        ("bücher.co.uk", "xn--bcher-kva", "co.uk"),
    ],
)
def test_multipart_tld_params(domain, sld, tld):
    assert Client._params(domain) == {"SLD": sld, "TLD": tld}


def test_resolve_doh_types_and_errors():
    import json as _json

    from namecheap_cli.client import _unescape_txt, resolve_doh

    def fake_opener(payload, *, size_override=None, exc=None):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = (
            size_override if size_override is not None else _json.dumps(payload).encode("utf-8")
        )
        return Mock(side_effect=exc, return_value=response)

    assert resolve_doh(
        "example.com",
        "A",
        opener=fake_opener(
            {
                "Status": 0,
                "Answer": [
                    {"type": 5, "data": "alias.example.net."},
                    {"type": 1, "data": "203.0.113.10"},
                ],
            }
        ),
    ) == ["203.0.113.10"]
    assert resolve_doh(
        "example.com",
        "AAAA",
        opener=fake_opener({"Status": 0, "Answer": [{"type": 28, "data": "2001:0db8::1"}]}),
    ) == ["2001:db8::1"]
    assert resolve_doh(
        "www.example.com",
        "CNAME",
        opener=fake_opener({"Status": 0, "Answer": [{"type": 5, "data": "Target.Example.NET."}]}),
    ) == ["target.example.net."]
    assert resolve_doh(
        "example.com",
        "MX",
        opener=fake_opener({"Status": 0, "Answer": [{"type": 15, "data": "10 Mail.Example.NET."}]}),
    ) == ["10 mail.example.net."]
    assert resolve_doh(
        "example.com",
        "TXT",
        opener=fake_opener(
            {
                "Status": 0,
                "Answer": [
                    {"type": 16, "data": '"v=spf1 " "include:_spf.example.net ~all"'},
                    {"type": 16, "data": '"quote=\\"ok\\" slash=\\\\ oct=\\059"'},
                ],
            }
        ),
    ) == ['quote="ok" slash=\\ oct=;', "v=spf1 include:_spf.example.net ~all"]
    assert resolve_doh("missing.example.com", "A", opener=fake_opener({"Status": 3})) == []
    assert _unescape_txt("plain-unquoted") == "plain-unquoted"

    with pytest.raises(CliError) as err:
        resolve_doh("example.com", "URL")
    assert err.value.code == "invalid_record"

    for rtype, bad_opener in (
        ("A", fake_opener({}, exc=TimeoutError())),
        ("A", fake_opener({}, size_override=b"x" * 65537)),
        ("A", fake_opener({}, size_override=b"not-json")),
        ("A", fake_opener({"Status": "bad"})),
        ("A", fake_opener({"Status": 2})),
        ("A", fake_opener({"Status": 0, "Answer": "bad"})),
        ("A", fake_opener({"Status": 0, "Answer": [{"type": 1, "data": "not-an-ip"}]})),
        ("AAAA", fake_opener({"Status": 0, "Answer": [{"type": 28, "data": "not-v6"}]})),
        ("MX", fake_opener({"Status": 0, "Answer": [{"type": 15, "data": "ten mail.example."}]})),
        ("MX", fake_opener({"Status": 0, "Answer": [{"type": 15, "data": "10"}]})),
    ):
        with pytest.raises(CliError) as err:
            resolve_doh("example.com", rtype, opener=bad_opener)
        assert err.value.code == "doh_error"
