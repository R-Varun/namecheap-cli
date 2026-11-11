# Contributing

Focused, well-tested pull requests are welcome. Open an issue first if you want
to propose a new provider command or alter the write-safety contract.

## Local Workflow

```sh
uv sync --extra dev
uv run pytest --cov=namecheap_cli --cov-report=term-missing
uv run ruff check .
uv run ruff format --check .
uv build && uv run python scripts/check_distribution.py
uv run python scripts/build_site.py
```

- Keep runtime dependencies at zero (`Python 3.11+` standard library only).
- Tests must run deterministically offline without real API keys or external network calls.
- Cover both success and error/recovery paths.
- Whenever CLI flags, subcommands, zone validation, or error codes change, regenerate the checked-in schema with `uv run namecheap schema > docs/schema.json` and update `docs/cli.md`.

## Safety Invariants

Do not introduce raw XML/API passthrough flags, automatic write retries,
command-line secret arguments, `.env` auto-loading, or silent defaults for
omitted email/provider metadata. Unknown provider fields must block record
writes rather than risking data loss.

Use RFC-reserved example domains (`example.com`, `example.net`, `example.org`)
and documentation IP blocks (`192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`,
`2001:db8::/32`) in all tests, docs, and commit messages. Never include real
account domains, credentials, or DNS backup snapshots.
