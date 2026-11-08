import json
import os

import pytest
from conftest import KEY

from namecheap_cli.config import Config, Credentials, config_path, setup_credentials, state_path
from namecheap_cli.errors import CliError
from namecheap_cli.storage import atomic_json, domain_lock, read_json


def test_config_and_state_environment_paths(tmp_path):
    assert (
        config_path({"XDG_CONFIG_HOME": str(tmp_path)})
        == tmp_path / "namecheap-cli" / "config.json"
    )
    assert state_path({"XDG_STATE_HOME": str(tmp_path)}) == tmp_path / "namecheap-cli"
    assert state_path({"NAMECHEAP_STATE_DIR": str(tmp_path)}) == tmp_path
    assert config_path({}).name == "config.json"


@pytest.mark.parametrize(
    "data",
    [
        [],
        {},
        {"schema_version": True, "allowed_domains": []},
        {"schema_version": 2, "allowed_domains": []},
        {"schema_version": 1, "allowed_domains": "example.com"},
        {"schema_version": 1, "allowed_domains": [], "extra": True},
    ],
)
def test_config_validation(tmp_path, data):
    path = tmp_path / "config.json"
    atomic_json(path, data)
    with pytest.raises(CliError):
        Config.load(path)


def test_missing_config_and_bad_json(tmp_path):
    with pytest.raises(CliError) as error:
        Config.load(tmp_path / "absent")
    assert error.value.code == "config_missing"
    bad = tmp_path / "bad.json"
    bad.write_text("{broken")
    with pytest.raises(CliError):
        read_json(bad)


def test_environment_credentials_and_profile_isolation(tmp_path):
    path = tmp_path / "config.json"
    env = {"NAMECHEAP_API_USER": "tester", "NAMECHEAP_API_KEY": KEY}
    creds = Credentials.load(path, "production", env)
    assert creds.username == "tester" and KEY not in repr(creds)
    with pytest.raises(CliError) as error:
        Credentials.load(path, "sandbox", env)
    assert error.value.exit_code == 66
    env.update(
        NAMECHEAP_SANDBOX_API_USER="sandbox",
        NAMECHEAP_SANDBOX_API_KEY="sandbox-fake-key",  # pragma: allowlist secret
    )
    assert Credentials.load(path, "sandbox", env).api_user == "sandbox"
    with pytest.raises(CliError):
        Credentials.load(path, "production", {"NAMECHEAP_API_USER": "tester"})


def test_file_credentials_permissions_and_values(tmp_path):
    file = tmp_path / "credentials-production.json"
    atomic_json(file, {"api_user": "tester", "api_key": KEY})
    assert Credentials.load(tmp_path / "config.json", "production", {}).api_key == KEY
    if os.name != "nt":
        file.chmod(0o644)
        with pytest.raises(CliError) as error:
            Credentials.load(tmp_path / "config.json", "production", {})
        assert error.value.code == "unsafe_credentials"
        file.chmod(0o600)
    for data in (
        [],
        {"api_user": "", "api_key": KEY},
        {"api_user": "tester", "api_key": KEY, "username": 42},
    ):
        atomic_json(file, data)
        with pytest.raises(CliError):
            Credentials.load(tmp_path / "config.json", "production", {})


def test_hidden_prompt_setup_and_non_terminal_refusal(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr("os.isatty", lambda fd: False)
    with pytest.raises(CliError) as error:
        setup_credentials(path, "production", "tester")
    assert error.value.code == "terminal_required"
    monkeypatch.setattr("os.isatty", lambda fd: True)
    monkeypatch.setattr("getpass.getpass", lambda text: "a" * 32)
    file = setup_credentials(path, "production", "tester")
    assert Credentials.load(path, "production", {}).api_key == "a" * 32
    with pytest.raises(CliError):
        setup_credentials(path, "production", "tester")
    monkeypatch.setattr("getpass.getpass", lambda text: "not a key")
    with pytest.raises(CliError):
        setup_credentials(path, "production", "tester", force=True)
    assert json.loads(file.read_text())["api_key"] == "a" * 32
    with pytest.raises(CliError):
        setup_credentials(path, "production", "invalid username", force=True)


def test_atomic_no_overwrite_and_symlink_refusal(tmp_path):
    file = tmp_path / "file.json"
    atomic_json(file, {"old": True}, replace=False)
    with pytest.raises(CliError):
        atomic_json(file, {"old": False}, replace=False)
    assert read_json(file) == {"old": True}
    link = tmp_path / "link.json"
    try:
        link.symlink_to(file)
    except OSError:
        pytest.skip("symlinks unavailable for this Windows user")
    with pytest.raises(CliError):
        atomic_json(link, {}, replace=True)
    (tmp_path / "credentials-production.json").symlink_to(file)
    with pytest.raises(CliError):
        Credentials.load(tmp_path / "config.json", "production", {})
    assert read_json(file) == {"old": True}


def test_locks_release_after_exception(tmp_path):
    path = tmp_path / "locks" / "domain.lock"
    with pytest.raises(RuntimeError), domain_lock(path):
        raise RuntimeError("synthetic")
    with domain_lock(path):
        assert path.exists()
