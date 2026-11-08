# Command contract

Version 1 output is one JSON document on stdout. Success uses `schema_version`,
`ok`, `command`, `data`, and `warnings`. Failure uses the same version plus
`error.code`, `error.message`, `error.details`, and `error.retryable`. No extra
progress text is mixed into JSON. Help and --version use conventional text.
Use `--human` for a short terminal view, or `--json` to state the default.

Commands:

- `config init --domain example.com` creates an allowlist.
- `config allow example.net` adds a domain; `config show` displays settings.
- `auth setup --username USER` stores a key from a hidden terminal prompt.
- `doctor` checks configuration and read-only API access.
- `domains list [--all]` lists allowed domains, or the whole account explicitly.
- `domains check example.com example.net` checks registration availability.
- `dns list example.com` returns the zone and its state hash.
- `dns add|set example.com HOST TYPE VALUE [--ttl N] [--mxpref N]` edits a record.
- `dns remove example.com HOST TYPE [--value VALUE]` removes matching records.
- `dns apply example.com FILE` replaces records using a zone JSON file.
- `ns list example.com` returns nameservers and their state hash.
- `ns set example.com --custom NS1 NS2` or `--default` changes delegation.
- `restore BACKUP` previews restoration of a DNS or nameserver backup.
- `schema` returns the command catalogue and machine-readable output schemas.

Mutation commands accept `--yes --if-match HASH`. Without --yes they only read
and compute a preview. Preview data includes `state_hash`, `changed`, `before`,
`after`, and `diff`. Confirmed writes include `verified` and `recovery`.

Exit codes: 0 success (including dry runs and no changes); 1 provider/network
failure; 2 invalid input/configuration; 3 blocked operation (scope, conflict,
busy lock, or unsupported metadata); 4 submitted write not verified; 66 missing
credentials; 130 interruption. Inspect error.code for the specific cause.
