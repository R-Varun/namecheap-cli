import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import DOMAIN, KEY

from namecheap_cli.cli import build_parser, main, schema


@pytest.fixture
def run(env, factory, capsys):
    def invoke(*args, custom_env=None):
        code = main(
            list(args), env=env if custom_env is None else custom_env, client_factory=factory
        )
        out = capsys.readouterr()
        assert KEY not in out.out + out.err and not out.err
        return code, json.loads(out.out)

    return invoke


def test_help_and_version_as_installed_module():
    for args in (["--help"], ["dns", "set", "--help"], ["--version"]):
        result = subprocess.run(
            [sys.executable, "-m", "namecheap_cli", *args], capture_output=True, text=True
        )
        assert result.returncode == 0 and result.stdout and not result.stderr


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["unknown"],
        ["dns"],
        ["dns", "set"],
        ["dns", "set", DOMAIN, "@", "A", "192.0.2.1", "--ttl", "bad"],
    ],
)
def test_usage_errors_are_json(run, args):
    code, result = run(*args)
    assert code == 2 and result["error"]["code"] == "usage" and result["schema_version"] == 1


def test_local_config_commands_without_credentials(run, tmp_path):
    path = tmp_path / "standalone" / "config.json"
    code, result = run("--config", str(path), "config", "init", "--domain", DOMAIN, custom_env={})
    assert code == 0 and result["data"]["allowed_domains"] == [DOMAIN]
    code, _ = run("config", "allow", "example.net", "--config", str(path), custom_env={})
    assert code == 0
    code, result = run("config", "--config", str(path), "show", custom_env={})
    assert result["data"]["allowed_domains"] == [DOMAIN, "example.net"]
    code, result = run("--config", str(path), "config", "init", custom_env={})
    assert code == 2 and result["error"]["code"] == "file_exists"


def test_scope_checked_before_credentials_or_network(run, api):
    code, result = run("dns", "list", "outside.example")
    assert code == 3 and result["error"]["code"] == "out_of_scope" and not api.requests


def test_missing_credentials(run, env):
    env = {k: v for k, v in env.items() if not k.endswith("API_KEY") and not k.endswith("API_USER")}
    code, result = run("dns", "list", DOMAIN, custom_env=env)
    assert code == 66 and result["error"]["code"] == "credentials_missing"


def test_doctor_domains_and_check(run):
    assert run("doctor")[1]["data"]["api"] == "ok"
    code, result = run("domains", "list")
    assert code == 0 and len(result["data"]["domains"]) == 1 and result["data"]["hidden_count"] == 1
    assert len(run("domains", "list", "--all")[1]["data"]["domains"]) == 2
    assert run("domains", "check", DOMAIN)[1]["data"]["results"][0]["available"]


def test_cli_preview_apply_restore(run):
    assert run("dns", "list", DOMAIN)[1]["data"]["zone"]["domain"] == DOMAIN
    args = ["dns", "set", DOMAIN, "@", "A", "192.0.2.10"]
    code, plan = run(*args)
    assert code == 0 and plan["data"]["dry_run"]
    code, applied = run(*args, "--yes", "--if-match", plan["data"]["state_hash"])
    assert code == 0 and applied["data"]["verified"]
    file = applied["data"]["recovery"]["backup_file"]
    _, undo = run("restore", file)
    assert run("restore", file, "--yes", "--if-match", undo["data"]["state_hash"])[0] == 0


def test_cli_add_remove_apply(run, tmp_path):
    _, data = run("dns", "list", DOMAIN)
    file = tmp_path / "zone.json"
    file.write_text(json.dumps(data["data"]["zone"]))
    assert not run("dns", "apply", DOMAIN, str(file))[1]["data"]["changed"]
    assert run("dns", "add", DOMAIN, "@", "TXT", "hello")[1]["data"]["changed"]
    assert not run("dns", "remove", DOMAIN, "missing", "TXT")[1]["data"]["changed"]
    assert run("dns", "remove", DOMAIN, "www", "CNAME", "--value", "parking.example.net")[1][
        "data"
    ]["changed"]


def test_cli_nameservers(run):
    assert run("ns", "list", DOMAIN)[0] == 0
    _, plan = run("ns", "set", DOMAIN, "--custom", "ns1.example.org", "ns2.example.org")
    assert run(
        "ns",
        "set",
        DOMAIN,
        "--custom",
        "ns1.example.org",
        "ns2.example.org",
        "--yes",
        "--if-match",
        plan["data"]["state_hash"],
    )[1]["data"]["verified"]


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"domain": DOMAIN, "hosts": [{}]},
        {
            "domain": DOMAIN,
            "hosts": [{"name": "@", "type": "A", "address": "192.0.2.1", "ttl": "bad"}],
        },
    ],
)
def test_malformed_json_no_traceback(run, tmp_path, data):
    file = tmp_path / "bad.json"
    file.write_text(json.dumps(data))
    code, error = run("dns", "apply", DOMAIN, str(file))
    assert code == 2 and not error["ok"]


def test_json_redacts_synthetic_provider_echo(run, api):
    api.hook = lambda form: (
        f'<ApiResponse Status="ERROR"><Error Number="1011102">{KEY}</Error></ApiResponse>'
    )
    code, result = run("dns", "list", DOMAIN)
    assert code == 1 and "[REDACTED]" in str(result)


def test_schema_discovery(run):
    code, result = run("schema", custom_env={})
    assert code == 0
    data = result["data"]
    assert data["output_schema_version"] == 1
    assert {c["command"] for c in data["commands"]} >= {"dns set", "dns list", "restore", "schema"}
    assert data == schema(build_parser())
    saved = Path(__file__).resolve().parents[1] / "docs" / "schema.json"
    assert json.loads(saved.read_text())["data"] == data


def test_human_output_does_not_mix_with_json(env, factory, capsys):
    assert (
        main(
            ["--human", "dns", "set", DOMAIN, "@", "A", "192.0.2.10"],
            env=env,
            client_factory=factory,
        )
        == 0
    )
    assert "example.com: dry run" in capsys.readouterr().out


@pytest.mark.parametrize(
    "args",
    [
        ["--s", "dns", "list", DOMAIN],
        ["domains", "list", "--a"],
        ["dns", "set", DOMAIN, "@", "A", "192.0.2.1", "--y"],
    ],
)
def test_prefix_abbreviations_are_refused(run, args):
    code, result = run(*args)
    assert code == 2 and result["error"]["code"] == "usage"


def test_config_disallow_and_noop(run, tmp_path):
    path = tmp_path / "cfg" / "config.json"
    run(
        "--config",
        str(path),
        "config",
        "init",
        "--domain",
        DOMAIN,
        "--domain",
        "example.net",
        custom_env={},
    )
    code, result = run(
        "--config", str(path), "config", "disallow", "EXAMPLE.NET.", "absent.example", custom_env={}
    )
    assert code == 0
    assert result["data"]["allowed_domains"] == [DOMAIN]
    assert result["data"]["removed"] == ["example.net"]


def test_dns_apply_stdin_and_validation_before_credentials(env, factory, capsys):
    import io

    zone_payload = {
        "domain": DOMAIN,
        "email_type": "FWD",
        "hosts": [{"name": "@", "type": "A", "address": "192.0.2.55", "ttl": 1800}],
    }
    code = main(
        ["dns", "apply", DOMAIN, "-"],
        env=env,
        client_factory=factory,
        stdin=io.StringIO(json.dumps(zone_payload)),
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0 and payload["data"]["changed"] and payload["data"]["dry_run"]

    # Local record/flag validation runs before credential lookup or IP detection.
    no_creds_env = {"NAMECHEAP_CONFIG_FILE": env["NAMECHEAP_CONFIG_FILE"]}
    for bad_argv in (
        ["dns", "set", DOMAIN, "-bad", "A", "192.0.2.1"],
        ["dns", "set", DOMAIN, "@", "A", "192.0.2.1", "--mxpref", "10"],
        ["dns", "set", DOMAIN, "@", "A", "192.0.2.1", "--yes"],
        ["dns", "set", DOMAIN, "@", "A", "192.0.2.1", "--if-match", "bad"],
        ["ns", "set", DOMAIN, "--custom", "only-one.example.net"],
        ["dns", "check", DOMAIN, "--host", "-bad"],
    ):
        code = main(bad_argv, env=no_creds_env, client_factory=factory)
        err = json.loads(capsys.readouterr().out)
        assert code == 2 and err["error"]["code"] != "credentials_missing"


def test_cli_dns_check_and_schema_groups(env, factory, api, capsys):
    from namecheap_cli.records import Host, Zone

    api.zone = Zone(
        DOMAIN,
        (
            Host("@", "A", "192.0.2.10"),
            Host("@", "URL", "https://www.example.com/"),
        ),
        "FWD",
    )
    code = main(
        ["dns", "check", DOMAIN],
        env=env,
        client_factory=factory,
        doh_resolver=lambda qname, rtype: ["192.0.2.10"],
    )
    res = json.loads(capsys.readouterr().out)
    assert code == 0 and res["data"]["propagated"] is True
    assert res["data"]["checked_count"] == 1 and res["data"]["synthetic_count"] == 1

    data = schema(build_parser())
    ns_set = next(c for c in data["commands"] if c["command"] == "ns set")
    assert any(
        g["required"] and "--custom" in g["flags"] for g in ns_set["mutually_exclusive_groups"]
    )
    cfg_init = next(c for c in data["commands"] if c["command"] == "config init")
    domain_arg = next(a for a in cfg_init["arguments"] if a["name"] == "domain")
    assert domain_arg["repeatable"] is True
    assert "stale_state" in data["error_codes"]
