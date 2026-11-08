# Namecheap CLI

An unofficial Namecheap DNS CLI for agents, scripts, and people. Python 3.11+
with no runtime dependencies. Public repository: `R-Varun/namecheap-cli`.

Read README.md and docs/cli.md before changing the command contract. Read
docs/safety.md before changing writes, email handling, backups, or transport.

- Install development tools: `uv sync --extra dev`.
- Test: `uv run pytest --cov=namecheap_cli --cov-report=term-missing`.
- Lint: `uv run ruff check .` and `uv run ruff format --check .`.
- Build: `uv build`; inspect and install the wheel in a clean environment.
- Tests use synthetic data and a loopback API. Never require real credentials.

Code lives in src/namecheap_cli. cli.py owns argument parsing and the versioned
JSON envelope; service.py owns the write lifecycle; client.py owns XML/HTTP;
records.py owns DNS semantics; config.py and storage.py own local files.

Every DNS/nameserver write requires --yes and the --if-match hash from a dry
run. Hold a per-account, environment, and domain process lock across the whole
transaction. Save backup and pending journal before submission. Never retry a
write automatically. An unverified outcome must include recovery details.
Locks only coordinate processes sharing the same local state directory. The
provider has no conditional write used here: document the external-writer race.

Preserve TXT content. Compare TTL, MX preference, multiplicity, and email mode.
Do not guess omitted email metadata or discard unsupported provider fields.
Unknown metadata must block writes until its semantics are implemented/tested.
Never accept an arbitrary endpoint or read shell dotenv files in the public CLI.
Redact credentials from provider errors before emitting output.

Do not commit credentials, real account domains, private paths, DNS backups,
runtime journals, or source from unrelated projects. Public examples use
reserved example domains and documentation IP ranges. Keep CLAUDE.md as a
relative symlink to this file. Keep the local personal-project registry current.

This repository began as an explicitly authorized, sanitized extraction of a
personally owned local tool. Public code is canonical; private account adapters
remain outside this repository. Do not pull private configuration into it.

No research effort packet is associated with this project.
