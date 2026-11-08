"""Inspect the wheel and prove the installed command works outside the checkout."""

import email
import json
import os
import subprocess
import tempfile
import venv
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
wheels = sorted((root / "dist").glob("*.whl"))
assert len(wheels) == 1, "Build exactly one wheel before checking it."
wheel = wheels[0]
with zipfile.ZipFile(wheel) as archive:
    files = archive.namelist()
    assert all(name.startswith("namecheap_cli/") or ".dist-info/" in name for name in files)
    metadata = email.message_from_bytes(
        archive.read(next(n for n in files if n.endswith("/METADATA")))
    )
    assert metadata["Name"] == "namecheap-agent-cli"
    assert all("extra ==" in value for value in metadata.get_all("Requires-Dist", []))
    assert any(name.endswith("/LICENSE") for name in files)
    entry = archive.read(next(n for n in files if n.endswith("/entry_points.txt"))).decode()
    assert "namecheap = namecheap_cli.cli:main" in entry

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
        [str(command), "--config", str(config), "config", "init", "--domain", "example.com"],
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

print("Wheel contents, zero runtime dependencies, clean install, and installed CLI checked.")
