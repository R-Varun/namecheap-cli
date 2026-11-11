"""Inspect wheel and sdist artifacts and prove the installed CLI works outside the checkout."""

import email
import json
import os
import subprocess
import tarfile
import tempfile
import tomllib
import venv
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
wheels = sorted((root / "dist").glob(f"namecheap_agent_cli-{version}-*.whl"))
sdists = sorted((root / "dist").glob(f"namecheap_agent_cli-{version}.tar.gz"))
assert len(wheels) == 1, f"Expected one wheel for v{version} in dist/, found {len(wheels)}."
assert len(sdists) == 1, f"Expected one sdist for v{version} in dist/, found {len(sdists)}."
wheel = wheels[0]
sdist = sdists[0]

with zipfile.ZipFile(wheel) as archive:
    files = archive.namelist()
    assert all(name.startswith("namecheap_cli/") or ".dist-info/" in name for name in files)
    metadata = email.message_from_bytes(
        archive.read(next(n for n in files if n.endswith("/METADATA")))
    )
    assert metadata["Name"] == "namecheap-agent-cli"
    assert metadata["Version"] == version
    assert all("extra ==" in value for value in metadata.get_all("Requires-Dist", []))
    assert any(name.endswith("/LICENSE") for name in files)
    entry = archive.read(next(n for n in files if n.endswith("/entry_points.txt"))).decode()
    assert "namecheap = namecheap_cli.cli:main" in entry

forbidden_tokens = ("credentials", "backups/", "journals/", ".env")
with tarfile.open(sdist, "r:gz") as tar:
    names = tar.getnames()
    assert any(name.endswith("/README.md") for name in names)
    assert any(name.endswith("/LICENSE") for name in names)
    assert any(name.endswith("/skills/namecheap-dns/SKILL.md") for name in names)
    assert not any(tok in name.lower() for name in names for tok in forbidden_tokens)

with tempfile.TemporaryDirectory(prefix="namecheap-wheel-") as temp:
    base = Path(temp)
    environment = base / "venv"
    venv.EnvBuilder(with_pip=True).create(environment)
    bin_dir = environment / ("Scripts" if os.name == "nt" else "bin")
    python = bin_dir / ("python.exe" if os.name == "nt" else "python")
    command = bin_dir / ("namecheap.exe" if os.name == "nt" else "namecheap")
    env = {key: value for key, value in os.environ.items() if not key.startswith("NAMECHEAP_")}
    env.pop("PYTHONPATH", None)
    subprocess.run(
        [str(python), "-m", "pip", "install", "--no-deps", str(wheel)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
        cwd=base,
    )
    result = subprocess.run(
        [str(command), "schema"], check=True, capture_output=True, text=True, env=env, cwd=base
    )
    assert json.loads(result.stdout)["ok"]
    config = base / "configuration" / "config.json"
    result = subprocess.run(
        [
            str(command),
            "--config",
            str(config),
            "config",
            "init",
            "--domain",
            "example.com",
            "--domain",
            "example.net",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
        cwd=base,
    )
    assert json.loads(result.stdout)["data"]["allowed_domains"] == ["example.com", "example.net"]
    result = subprocess.run(
        [str(command), "--config", str(config), "config", "disallow", "example.net"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
        cwd=base,
    )
    assert json.loads(result.stdout)["data"]["allowed_domains"] == ["example.com"]
    result = subprocess.run(
        [str(command), "--config", str(config), "dns", "list", "outside.example"],
        capture_output=True,
        text=True,
        env=env,
        cwd=base,
    )
    assert result.returncode == 3 and json.loads(result.stdout)["error"]["code"] == "out_of_scope"

print(f"Wheel & sdist v{version}, zero runtime deps, clean install, and CLI contract checked.")
