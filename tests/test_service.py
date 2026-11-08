import json
import os
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import DOMAIN

from namecheap_cli.errors import CliError
from namecheap_cli.records import Host, Zone


def preview(service, **kwargs):
    return service.dns_edit(DOMAIN, "set", value="192.0.2.10", **kwargs)


def test_dry_run_does_not_write(service, api):
    before = api.zone
    plan = preview(service)
    assert plan["dry_run"] and plan["changed"] and not plan["verified"]
    assert api.zone == before and not api.writes
    assert not list(service.state_dir.rglob("*.json"))


def test_apply_backup_verify_and_restore(service, api):
    before = api.zone
    plan = preview(service)
    result = preview(service, yes=True, if_match=plan["state_hash"])
    assert result["verified"] and not result["dry_run"]
    recovery = result["recovery"]
    backup, journal = Path(recovery["backup_file"]), Path(recovery["journal_file"])
    assert json.loads(backup.read_text())["before"] == before.to_json()
    assert json.loads(journal.read_text())["status"] == "verified"
    if os.name != "nt":
        assert backup.stat().st_mode & 0o777 == 0o600
    undo = service.restore(backup)
    assert undo["dry_run"] and undo["changed"]
    restored = service.restore(backup, yes=True, if_match=undo["state_hash"])
    assert restored["verified"] and api.zone.canonical() == before.canonical()


def test_yes_requires_preview(service, api):
    with pytest.raises(CliError) as error:
        preview(service, yes=True)
    assert error.value.code == "preview_required"
    assert not api.requests


def test_stale_preview_blocks_write(service, api):
    plan = preview(service)
    api.zone = replace(api.zone, hosts=api.zone.hosts + (Host("@", "TXT", "external edit"),))
    with pytest.raises(CliError) as error:
        preview(service, yes=True, if_match=plan["state_hash"])
    assert error.value.code == "stale_state" and not api.writes


def test_external_change_during_preparation_blocks_write(service, api):
    plan = preview(service)
    reads = 0

    def hook(form):
        nonlocal reads
        if form["Command"].endswith("getHosts"):
            reads += 1
            if reads == 2:
                api.zone = replace(
                    api.zone, hosts=api.zone.hosts + (Host("@", "TXT", "external edit"),)
                )

    api.hook = hook
    with pytest.raises(CliError) as error:
        preview(service, yes=True, if_match=plan["state_hash"])
    assert error.value.code == "stale_state" and not api.writes
    assert any(h.address == "external edit" for h in api.zone.hosts)
    journal = next(service.state_dir.rglob("journals/**/*.json"))
    assert json.loads(journal.read_text())["status"] == "conflict"


@pytest.mark.parametrize("field,value", [("ttl", 600), ("mxpref", 99), ("duplicate", None)])
def test_wrong_attributes_and_multiplicity_fail_verification(service, api, field, value):
    api.zone = Zone(DOMAIN, (Host("@", "MX", "mx.example.net.", ttl=300, mxpref=5),), "MX")
    plan = service.dns_edit(DOMAIN, "set", rtype="MX", value="mx.example.net", ttl=900, mxpref=10)
    api.corrupt = lambda zone: replace(
        zone,
        hosts=zone.hosts * 2
        if field == "duplicate"
        else (replace(zone.hosts[0], **{field: value}),),
    )
    with pytest.raises(CliError) as error:
        service.dns_edit(
            DOMAIN,
            "set",
            rtype="MX",
            value="mx.example.net",
            ttl=900,
            mxpref=10,
            yes=True,
            if_match=plan["state_hash"],
        )
    assert error.value.exit_code == 4 and error.value.code == "write_unverified"
    assert error.value.details["cause"]["code"] == "verification_mismatch"
    assert Path(error.value.details["recovery"]["backup_file"]).exists()


def test_failed_verification_returns_recovery_after_applied_write(service, api):
    plan = preview(service)

    def hook(form):
        if form["Command"].endswith("getHosts") and api.writes:
            return (
                '<ApiResponse Status="ERROR"><Error Number="999">'
                "Synthetic read failure</Error></ApiResponse>"
            )

    api.hook = hook
    with pytest.raises(CliError) as error:
        preview(service, yes=True, if_match=plan["state_hash"])
    assert error.value.exit_code == 4
    assert any(h.address == "192.0.2.10" for h in api.zone.hosts)
    recovery = error.value.details["recovery"]
    assert Path(recovery["backup_file"]).exists()
    assert json.loads(Path(recovery["journal_file"]).read_text())["status"] == "unverified"
    assert recovery["restore_preview_argv"][0] == "namecheap"


def test_backup_is_durable_before_submission(service, api):
    plan = preview(service)
    observed = []

    def hook(form):
        if form["Command"].endswith("setHosts"):
            backups = list(service.state_dir.rglob("backups/**/*.json"))
            journals = list(service.state_dir.rglob("journals/**/*.json"))
            observed.append((len(backups), json.loads(journals[0].read_text())["status"]))

    api.hook = hook
    preview(service, yes=True, if_match=plan["state_hash"])
    assert observed == [(1, "submitted")]


def test_interrupt_during_submission_keeps_recovery(service, monkeypatch):
    plan = preview(service)

    def interrupt(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(service.client, "set_hosts", interrupt)
    with pytest.raises(CliError) as error:
        preview(service, yes=True, if_match=plan["state_hash"])
    assert error.value.exit_code == 130
    assert Path(error.value.details["recovery"]["backup_file"]).exists()


def test_no_change_does_not_write(service, api):
    result = service.dns_edit(DOMAIN, "add", name="www", rtype="CNAME", value="parking.example.net")
    assert not result["changed"] and not api.writes


@pytest.mark.parametrize(
    "attrs",
    [
        {"IsDDNSEnabled": "true"},
        {"IsActive": "false"},
        {"AssociatedAppTitle": "managed"},
        {"FutureField": "unknown"},
    ],
)
def test_metadata_protection(service, api, attrs):
    api.host_attrs = attrs
    assert service.dns_list(DOMAIN)["unsupported_metadata"]
    with pytest.raises(CliError) as error:
        preview(service)
    assert error.value.code == "unsupported_metadata" and not api.writes


def test_unknown_email_blocks_without_guessing(service, api):
    api.zone = replace(api.zone, email_type=None)
    with pytest.raises(CliError) as error:
        preview(service)
    assert error.value.code == "email_mode_unknown" and not api.writes
    assert preview(service, email_type="FWD")["warnings"]


def test_nameservers_backup_and_restore(service, api):
    before = api.ns.copy()
    plan = service.ns_set(DOMAIN, servers=["ns1.example.org", "ns2.example.org"])
    assert plan["dry_run"] and not api.writes
    result = service.ns_set(
        DOMAIN,
        servers=["ns1.example.org", "ns2.example.org"],
        yes=True,
        if_match=plan["state_hash"],
    )
    assert result["verified"] and api.ns["mode"] == "custom"
    backup = Path(result["recovery"]["backup_file"])
    undo = service.restore(backup)
    service.restore(backup, yes=True, if_match=undo["state_hash"])
    assert api.ns == before


@pytest.mark.parametrize(
    "servers", [[], ["ns1.example.org"], ["ns1.example.org"] * 2, ["ns1.example.org", "invalid"]]
)
def test_invalid_nameservers(service, api, servers):
    with pytest.raises(CliError):
        service.ns_set(DOMAIN, servers=servers)
    assert not api.requests


def test_restore_rejects_wrong_account_and_profile(service, tmp_path):
    file = tmp_path / "backup.json"
    file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "dns",
                "profile": "sandbox",
                "account_id": "someone-else",
                "before": {"domain": DOMAIN},
            }
        )
    )
    with pytest.raises(CliError) as error:
        service.restore(file)
    assert error.value.code == "invalid_backup"


def test_concurrent_process_is_refused_and_sequential_changes_preserved(service, api, config):
    plan = preview(service)
    submitting, release = threading.Event(), threading.Event()

    def hook(form):
        if form["Command"].endswith("setHosts"):
            submitting.set()
            assert release.wait(15)

    api.hook = hook
    script = """
import json,sys
from pathlib import Path
from namecheap_cli.config import Config,Credentials
from namecheap_cli.client import Client
from namecheap_cli.service import Service
from namecheap_cli.errors import CliError
config,endpoint,state,expected=sys.argv[1:]
api=Client(Credentials("tester","synthetic-test-key-not-a-credential","tester"),"127.0.0.1",endpoint=endpoint,allow_loopback=True,interval=0)
service=Service(Config.load(Path(config)),api,Path(state))
try:
    result=service.dns_edit("example.com","set",value="192.0.2.10",yes=True,if_match=expected)
    print(json.dumps(result))
except CliError as exc:
    print(json.dumps(exc.to_json()))
    sys.exit(exc.exit_code)
"""
    args = [
        sys.executable,
        "-c",
        script,
        str(config.path),
        api.url,
        str(service.state_dir.parents[1]),
        plan["state_hash"],
    ]
    first = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert submitting.wait(15)
        second = subprocess.run(args, capture_output=True, text=True, timeout=15)
        assert second.returncode == 3, second.stdout + second.stderr
        assert json.loads(second.stdout)["code"] == "busy"
    finally:
        release.set()
        out, err = first.communicate(timeout=15)
    assert first.returncode == 0, out + err
    assert len(api.writes) == 1
    api.hook = lambda form: None
    next_plan = service.dns_edit(DOMAIN, "set", name="www", value="192.0.2.20")
    service.dns_edit(
        DOMAIN, "set", name="www", value="192.0.2.20", yes=True, if_match=next_plan["state_hash"]
    )
    assert {h.address for h in api.zone.hosts} == {"192.0.2.10", "192.0.2.20"}
