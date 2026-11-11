# Command contract

Version 1 output is a single JSON document on `stdout`. Success emits
`schema_version`, `ok`, `command`, `data`, and `warnings`. Failure emits the
same `schema_version` plus `error.code`, `error.message`, `error.details`, and
`error.retryable`. No extra progress chatter is mixed into JSON. `--help` and
`--version` emit conventional text. Pass `--human` for a concise terminal view,
or `--json` to state the default explicitly. Long-option prefix abbreviations
(such as `--y` or `--s`) are rejected so scripts and agents never trigger
accidental flags.

## Commands (18)

- `config init --domain example.com` — Create configuration with an initial domain allowlist (`--domain` is repeatable).
- `config allow example.net ...` — Add one or more domains to the local allowlist.
- `config disallow example.net ...` — Remove one or more domains from the local allowlist.
- `config show` — Display active `schema_version`, `allowed_domains`, and configuration file `path`.
- `auth setup --username USER` — Read an API key from a hidden terminal prompt and store it in a `0600` local file.
- `doctor` — Validate local configuration, credentials, client IPv4, and read-only Namecheap API access.
- `domains list [--all]` — List allowlisted account domains, or `--all` to list every domain on the account.
- `domains check example.com example.net` — Check registration availability for up to 50 domains.
- `dns list example.com` — Return the BasicDNS zone (`hosts`, `email_type`) and its canonical `state_hash`.
- `dns add example.com HOST TYPE VALUE [--ttl N] [--mxpref N] [--email-type MODE]` — Append a host record when compatible records coexist.
- `dns set example.com HOST TYPE VALUE [--ttl N] [--mxpref N] [--email-type MODE]` — Set a host record, replacing same-type or conflicting records at `HOST`.
- `dns remove example.com HOST TYPE [--value VALUE] [--email-type MODE]` — Remove matching host records.
- `dns apply example.com FILE` — Replace the zone record set from a JSON file, or `-` to read zone JSON from `stdin`.
- `dns check example.com [--host HOST] [--type TYPE]` — Compare Namecheap's stored zone against live public DNS via Cloudflare DNS-over-HTTPS (`A`, `AAAA`, `CNAME`, `MX`, `NS`, `TXT`).
- `ns list example.com` — Return current nameservers, BasicDNS status, and `state_hash`.
- `ns set example.com (--custom NS1 NS2 ... | --default)` — Switch between custom nameservers and Namecheap BasicDNS.
- `restore BACKUP` — Preview or apply restoration of a saved DNS or nameserver backup JSON file.
- `schema` — Emit the machine-readable command catalogue, argument rules, zone JSON schema, and error-code reference.

## Mutation & Verification Contract

Mutation commands (`dns add`, `dns set`, `dns remove`, `dns apply`, `ns set`,
`restore`) require both `--yes` and `--if-match HASH` to execute a write:

- **Dry-run preview (default without `--yes`)**: Reads current state and returns
  `dry_run: true`, `changed`, `state_hash`, `before`, `after`, and `diff`.
- **Confirmed write (`--yes --if-match HASH`)**: Validates `HASH`, writes a
  backup if changes exist, submits once, and returns `dry_run: false`,
  `changed`, `verified: true`, `outcome` (`"verified"` when applied or `"noop"`
  when already in the desired state), and `recovery` (included whenever a backup
  and journal were created; omitted on a clean no-op).

Local input validation runs before credential loading or network IP detection so
malformed domains, host labels, record values, or flags fail immediately with
exit code `2`.

## Exit Codes

- `0` — Success (including dry-run previews and confirmed no-ops)
- `1` — Provider API, DoH, or network failure
- `2` — Invalid CLI arguments, record syntax, zone JSON, or local configuration
- `3` — Blocked by safety policy (`out_of_scope`, `stale_state`, `busy`, `external_dns`, `email_mode_unknown`, `unsupported_metadata`, `unsupported_record`)
- `4` — Write transaction aborted after backup or submitted without verification (`write_unverified`)
- `66` — Missing credentials (`credentials_missing`)
- `130` — Interrupted (`SIGINT`)
