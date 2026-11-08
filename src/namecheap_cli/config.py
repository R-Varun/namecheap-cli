"""Standalone configuration and credentials. No shell evaluation."""

import getpass
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import CliError
from .storage import atomic_json, read_json

_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def domain_name(value: str) -> str:
    if not isinstance(value, str):
        raise CliError("invalid_domain", "A domain must be a string.")
    try:
        result = value.strip().rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise CliError("invalid_domain", "Invalid international domain name.") from exc
    labels = result.split(".")
    if len(result) > 253 or len(labels) < 2 or not all(_LABEL.fullmatch(p) for p in labels):
        raise CliError("invalid_domain", f"Not a domain name: {value!r}")
    return result


def config_path(env: Any = None) -> Path:
    env = os.environ if env is None else env
    if env.get("NAMECHEAP_CONFIG_FILE"):
        return Path(env["NAMECHEAP_CONFIG_FILE"]).expanduser()
    base = env.get("XDG_CONFIG_HOME")
    if not base and os.name == "nt":
        base = env.get("APPDATA")
    return Path(base or Path.home() / ".config") / "namecheap-cli" / "config.json"


def state_path(env: Any = None) -> Path:
    env = os.environ if env is None else env
    if env.get("NAMECHEAP_STATE_DIR"):
        return Path(env["NAMECHEAP_STATE_DIR"]).expanduser()
    base = env.get("XDG_STATE_HOME")
    if not base and os.name == "nt":
        base = env.get("LOCALAPPDATA")
    return Path(base or Path.home() / ".local" / "state") / "namecheap-cli"


@dataclass(frozen=True)
class Config:
    path: Path
    allowed_domains: tuple[str, ...]

    @classmethod
    def load(cls, path: Path) -> "Config":
        if not path.exists():
            raise CliError(
                "config_missing",
                "Run `namecheap config init --domain example.com` first.",
                details={"path": str(path)},
            )
        data = read_json(path)
        if not isinstance(data, dict) or set(data) != {"schema_version", "allowed_domains"}:
            raise CliError(
                "invalid_config", "Config requires schema_version and allowed_domains only."
            )
        if data["schema_version"] != 1 or isinstance(data["schema_version"], bool):
            raise CliError("invalid_config", "Unsupported config schema_version; expected 1.")
        names = data["allowed_domains"]
        if not isinstance(names, list):
            raise CliError("invalid_config", "allowed_domains must be an array of domain names.")
        return cls(path, tuple(sorted({domain_name(name) for name in names})))

    def to_json(self) -> dict:
        return {"schema_version": 1, "allowed_domains": list(self.allowed_domains)}

    def require(self, value: str) -> str:
        name = domain_name(value)
        if name not in self.allowed_domains:
            raise CliError(
                "out_of_scope",
                f"{name} is outside the domain allowlist.",
                exit_code=3,
                details={"hint": f"If authorized, run `namecheap config allow {name}`."},
            )
        return name


@dataclass(frozen=True)
class Credentials:
    api_user: str
    api_key: str = field(repr=False)
    username: str

    @classmethod
    def load(cls, path: Path, profile: str, env: Any = None) -> "Credentials":
        env = os.environ if env is None else env
        prefix = "NAMECHEAP_SANDBOX_" if profile == "sandbox" else "NAMECHEAP_"
        user, key = env.get(prefix + "API_USER", ""), env.get(prefix + "API_KEY", "")
        username = env.get(prefix + "USERNAME", "")
        if user or key:
            if not user or not key:
                raise CliError(
                    "credentials_incomplete",
                    f"Set both {prefix}API_USER and {prefix}API_KEY.",
                    exit_code=66,
                )
        else:
            file = path.parent / f"credentials-{profile}.json"
            if not file.exists():
                raise CliError(
                    "credentials_missing",
                    "Set credential environment variables or run "
                    "`namecheap auth setup --username USER` with the same profile.",
                    exit_code=66,
                )
            if file.is_symlink():
                raise CliError("unsafe_credentials", "Credential file must not be a symlink.")
            if os.name != "nt" and stat.S_IMODE(file.stat().st_mode) & 0o077:
                raise CliError("unsafe_credentials", "Credential file must be private (chmod 600).")
            data = read_json(file)
            if not isinstance(data, dict):
                raise CliError("invalid_credentials", "Credential file must be a JSON object.")
            user, key = data.get("api_user", ""), data.get("api_key", "")
            username = data.get("username", user)
        if not all(isinstance(v, str) and v.strip() for v in (user, key)):
            raise CliError(
                "invalid_credentials",
                "The API user and key must be nonempty strings.",
                exit_code=66,
            )
        if not isinstance(username, str):
            raise CliError("invalid_credentials", "The account username must be a string.")
        return cls(user.strip(), key.strip(), username.strip() or user.strip())


def setup_credentials(path: Path, profile: str, username: str, *, force: bool = False) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", username):
        raise CliError(
            "invalid_username",
            "Use a Namecheap username with letters, digits, dot, dash, or underscore.",
        )
    file = path.parent / f"credentials-{profile}.json"
    if file.exists() and not force:
        raise CliError("file_exists", "Credential already exists; use --force to replace it.")
    if not os.isatty(0):
        raise CliError(
            "terminal_required",
            "A terminal is required for the hidden key prompt. "
            "Agents should use credential environment variables.",
        )
    key = getpass.getpass("Namecheap API key (hidden): ").strip()
    if not re.fullmatch(r"[A-Za-z0-9]{20,64}", key):
        raise CliError(
            "invalid_key", "Expected an API key with 20–64 letters and digits. Nothing was stored."
        )
    atomic_json(file, {"api_user": username, "api_key": key, "username": username}, replace=force)
    return file
