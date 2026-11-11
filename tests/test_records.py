from dataclasses import replace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from namecheap_cli.config import domain_name
from namecheap_cli.errors import CliError
from namecheap_cli.records import (
    Host,
    Zone,
    differences,
    edit,
    fingerprint,
    make_host,
    settle_email,
    validate,
)

D = "example.com"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Example.COM.", "example.com"),
        ("example.co.uk", "example.co.uk"),
        ("bücher.example", "xn--bcher-kva.example"),
    ],
)
def test_domain_normalization(value, expected):
    assert domain_name(value) == expected


@pytest.mark.parametrize(
    "value",
    ["", "localhost", "-bad.example", "bad..example", "a" * 64 + ".com", "has space.com", None],
)
def test_invalid_domains(value):
    with pytest.raises(CliError):
        domain_name(value)


@pytest.mark.parametrize(
    "rtype,value",
    [
        ("A", "invalid"),
        ("AAAA", "192.0.2.1"),
        ("AAAA", "fe80::1%eth0"),
        ("CNAME", "localhost"),
        ("URL", "https:///bad"),
        ("URL", "http://user:pw@example.com/"),
        ("URL", "http://example.com:99999/"),
        ("URL", "http://example.com:0/"),
        ("URL", "http://localhost/"),
        ("URL", "https://example.com/has space"),
        ("CAA", "0 issue example.com"),
    ],
)
def test_invalid_record_values(rtype, value):
    with pytest.raises(CliError):
        make_host(D, "@", rtype, value)


@pytest.mark.parametrize("ttl", [True, "300", 30, 60001, None])
def test_invalid_ttl(ttl):
    with pytest.raises(CliError):
        make_host(D, "@", "A", "192.0.2.1", ttl=ttl)


def test_names_and_targets():
    assert make_host(D, "WWW.example.com.", "cname", "@").to_json() == {
        "name": "www",
        "type": "CNAME",
        "address": "example.com.",
        "ttl": 1800,
    }
    assert make_host(D, "_acme-challenge", "TXT", "a").name == "_acme-challenge"
    assert make_host(D, "_25._tcp.mail", "TXT", "a").name == "_25._tcp.mail"
    assert make_host(D, "*.sub", "A", "192.0.2.1").name == "*.sub"
    assert make_host(D, "*", "A", "192.0.2.1").name == "*"
    assert make_host(D, "@", "URL301", "https://203.0.113.10:8443/ok").type == "URL301"
    for bad_host in ("bad name", "a" * 64, "-bad", "bad-", "sub.*", "_"):
        with pytest.raises(CliError):
            make_host(D, bad_host, "TXT", "a")


@given(
    st.text(
        alphabet=st.characters(blacklist_characters="\x00", blacklist_categories=("Cs",)),
        min_size=1,
    )
)
def test_txt_json_round_trip_preserves_content(value):
    zone = Zone(D, (make_host(D, "@", "TXT", value),), "FWD")
    assert Zone.from_json(D, zone.to_json()) == zone


def test_txt_whitespace_regression():
    host = make_host(D, "@", "TXT", "  meaningful spaces  ")
    assert host.address == "  meaningful spaces  "


@pytest.mark.parametrize(
    "data",
    [
        [],
        None,
        {},
        {"domain": D, "hosts": [{}]},
        {"domain": D, "hosts": [{"name": "@", "type": "A", "address": "192.0.2.1", "ttl": "bad"}]},
        {"domain": D, "hosts": [{"name": "@", "type": "TXT", "address": "v", "mxpref": 10}]},
        {"domain": D, "hosts": [], "typo": True},
        {"domain": D, "hosts": [], "email_type": "NONE"},
    ],
)
def test_malformed_zone_files_are_validation_errors(data):
    with pytest.raises(CliError):
        Zone.from_json(D, data)


def test_set_preserves_compatible_records():
    old = Zone(
        D,
        (
            Host("@", "URL", "https://example.net"),
            Host("@", "AAAA", "2001:db8::1"),
            Host("@", "TXT", "keep"),
        ),
    )
    # Existing conflicting state is repaired by set.
    new = edit(old, "set", make_host(D, "@", "A", "192.0.2.1"))
    assert {h.type for h in new.hosts} == {"AAAA", "TXT", "A"}
    validate(new)


def test_add_and_remove_are_idempotent():
    host = make_host(D, "@", "A", "192.0.2.1")
    zone = Zone(D, (host,))
    assert edit(zone, "add", host) == zone
    assert edit(zone, "remove", Host("missing", "A", ""), match_value=False) == zone
    with pytest.raises(CliError):
        edit(zone, "add", replace(host, ttl=300))


@pytest.mark.parametrize(
    "hosts",
    [
        (),
        (Host("@", "CNAME", "example.net."),),
        (Host("www", "CNAME", "example.net."), Host("www", "TXT", "x")),
        (Host("@", "A", "192.0.2.1"), Host("@", "URL", "https://example.net")),
        (Host("@", "A", "192.0.2.1"), Host("@", "A", "192.0.2.1")),
    ],
)
def test_unsafe_zones_refused(hosts):
    with pytest.raises(CliError):
        validate(Zone(D, hosts))


def test_unknown_record_or_metadata_refused():
    for zone in (
        Zone(D, (Host("@", "CAA", "x"),)),
        Zone(D, (Host("@", "TXT", "x"),), unsupported=("DDNS",)),
    ):
        with pytest.raises(CliError) as error:
            validate(zone)
        assert error.value.exit_code == 3


def test_complete_comparison():
    old = Zone(D, (Host("@", "MX", "mx.example.net.", ttl=300, mxpref=5),), "MX")
    for new in (
        replace(old, hosts=(replace(old.hosts[0], ttl=600),)),
        replace(old, hosts=(replace(old.hosts[0], mxpref=99),)),
        replace(old, hosts=old.hosts * 2),
        replace(old, email_type="OX"),
    ):
        assert old.canonical() != new.canonical()
        assert differences(old, new)
    same = replace(old, hosts=(replace(old.hosts[0], address="MX.Example.NET"),))
    assert old.canonical() == same.canonical()


def test_hash_order_and_environment():
    zone = Zone(D, (Host("@", "TXT", "b"), Host("@", "TXT", "a")), "FWD")
    reverse = replace(zone, hosts=tuple(reversed(zone.hosts)))
    assert fingerprint(zone.canonical(), "production") == fingerprint(
        reverse.canonical(), "production"
    )
    assert fingerprint(zone.canonical(), "production") != fingerprint(zone.canonical(), "sandbox")


def test_email_is_preserved_and_changes_are_explicit():
    old = Zone(D, (Host("@", "TXT", "x"),), "FWD")
    assert settle_email(old, old, None) == (old, [])
    mx = replace(old, hosts=old.hosts + (Host("@", "MX", "mx.example.net."),))
    with pytest.raises(CliError):
        settle_email(old, mx, None)
    new, warnings = settle_email(old, mx, "MX")
    assert new.email_type == "MX" and any("forwarding" in w for w in warnings)
    with pytest.raises(CliError) as error:
        settle_email(replace(old, email_type=None), old, None)
    assert error.value.code == "email_mode_unknown"
    with pytest.raises(CliError):
        settle_email(old, old, "MX")
