# Namecheap CLI

An unofficial Namecheap DNS CLI for agents, scripts, and developers. Python 3.11+
with zero runtime dependencies. Public repository: `R-Varun/namecheap-cli`.

Read `README.md` and `docs/cli.md` before changing the command contract. Read
`docs/safety.md` before changing writes, email handling, backups, or transport.

- Install development tools: `uv sync --extra dev`.
- Test: `uv run pytest --cov=namecheap_cli --cov-report=term-missing`.
- Lint: `uv run ruff check .` and `uv run ruff format --check .`.
- Schema sync: `uv run namecheap schema > docs/schema.json` (enforced by `tests/test_cli.py`).
- Build & verify dist/site: `uv build && uv run python scripts/check_distribution.py && uv run python scripts/build_site.py`.
- Tests use synthetic data and loopback HTTP servers. Never require real credentials or live network access.

Code lives in `src/namecheap_cli`:
- `cli.py` owns strict argument parsing (`allow_abbrev=False`), offline pre-validation, schema discovery (`namecheap schema`), and the versioned JSON envelope.
- `service.py` owns the guarded write lifecycle, recovery journals, and `dns check` live propagation comparison.
- `client.py` owns Namecheap XML/HTTP transport plus zero-dependency Cloudflare DNS-over-HTTPS resolution (`resolve_doh`).
- `records.py` owns DNS record validation, conflict semantics, and canonical state hashing.
- `config.py` and `storage.py` own allowlists, credentials, locks, backups, and bounded JSON input (`read_json`, `read_json_stdin`).
- `skills/namecheap-dns/SKILL.md` provides the portable Agent Skill distributed with the repository and sdist.

Every DNS/nameserver write requires `--yes` and the `--if-match` hash from a dry
run. Hold a per-account, environment, and domain process lock across the whole
transaction. Save backup and pending journal before submission. Never retry a
write automatically. Any unverified or aborted post-backup outcome must include
recovery details. Locks only coordinate processes sharing the same local state
directory; because the provider API has no conditional write, keep documenting
the external-writer race.

Preserve TXT content. Compare TTL, MX preference, multiplicity, and email mode.
Do not guess omitted email metadata or discard unsupported provider fields.
Unknown metadata must block writes until its semantics are implemented and tested.
Never accept an arbitrary endpoint or read shell dotenv files in the public CLI.
Redact credentials from provider errors before emitting output.

Do not commit credentials, real account domains, private paths, DNS backups,
runtime journals, or source from unrelated projects. Public examples use
reserved example domains and documentation IP ranges. Keep `CLAUDE.md` as a
relative symlink to this file. Keep the local personal-project registry current.

This repository began as an explicitly authorized, sanitized extraction of a
personally owned local tool. Public code is canonical; private account adapters
remain outside this repository. Do not pull private configuration into it.

No research effort packet is associated with this project.
