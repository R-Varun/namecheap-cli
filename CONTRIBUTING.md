# Contributing

Small, tested changes are welcome. Start with an issue for a new provider
operation or a change to write behavior.

Use `uv sync --extra dev`, then run the test, lint, format, and build commands
in the README. Tests must work without real credentials or internet access.
Test failure paths. If you change the command contract, update its docs and
the generated schema check together.

Do not add a raw-API escape hatch, automatic write retries, credentials in
command-line flags, or assumptions about omitted provider fields. Keep
provider-backed evidence separate from fake API tests. A test double proves
local behavior; it does not prove the live provider's semantics.

Use reserved example domains and documentation IP ranges. Do not submit account
data, keys, backups, or customer DNS snapshots. Keep prose short and say what
changed and how you checked it.
