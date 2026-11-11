# How writes work

Every DNS or nameserver mutation is a read-only dry run until you pass both
`--yes` and the `--if-match` SHA-256 state hash returned by its preview. The
hash binds the operation to the observed provider state and target environment
(`production` vs `sandbox`). It is an optimistic concurrency check, not an
authentication token.

## Transaction Lifecycle

When `--yes --if-match HASH` is supplied and the target state differs from the
current state, the CLI executes the following steps while holding an exclusive
local lock for `(account, environment, domain)`:

1. Reads the current provider state and verifies it matches `--if-match`.
2. Computes the full replacement zone or nameserver list and validates all records.
3. Writes a `0600` backup snapshot and a `"prepared"` journal entry locally.
4. Re-reads provider state right before submission to catch concurrent external changes (marking the journal `"aborted"` and returning recovery paths if that check fails).
5. Submits the provider write (`domains.dns.setHosts`, `domains.dns.setCustom`, or `domains.dns.setDefault`) exactly once—writes are never retried automatically.
6. Reads the state back from Namecheap and verifies every host record, TTL, MX preference, multiplicity, and email mode.

If the provider rejects the submission (`outcome: "rejected"`), if transport
times out (`outcome: "unknown"`), or if read-back differs from the intended zone,
the CLI exits with code `4` (`write_unverified`) and returns `error.details.recovery`
pointing to both the backup snapshot and journal file. If a confirmed mutation
computes zero diff against the live state, no provider write is sent and the CLI
returns `verified: true`, `changed: false`, and `outcome: "noop"`.

## Whole-Zone Replacement & Concurrency

Namecheap's `setHosts` replaces every BasicDNS host record in a single call. The
local lock coordinates concurrent CLI processes sharing the same state directory,
and the pre-submission re-read narrows the window for concurrent edits from
another machine or the web dashboard. Because Namecheap's XML API offers no
server-side compare-and-swap primitive, avoid running external DNS writers
concurrently on the same domain.

## Fidelity & Guardrails

- **Record comparison**: TTL, MX preference (`MX` only), record multiplicity, and email mode are all verified. Hostname case and trailing dots normalize canonically, while `TXT` payloads are preserved verbatim.
- **Unsupported settings**: Zones with unknown record types (such as `CAA` or `SRV` returned outside BasicDNS host support) or dashboard-only flags (such as Dynamic DNS or inactive rows) can be inspected with `dns list`, but writes are blocked (`unsupported_record` / `unsupported_metadata`) rather than silently dropping fields.
- **Email routing safety**: Missing email metadata never implies "disable mail." If the current email mode cannot be preserved safely, the CLI blocks the write (`email_mode_unknown`). Changing mail routing or adding custom `MX` records under non-`MX` modes requires an explicit `--email-type MX|MXE|FWD|OX`.
- **Public DNS propagation**: Post-write verification confirms Namecheap's API stored your records. Use `namecheap dns check <domain>` to verify live answers from public DNS-over-HTTPS resolvers.
- **Sandbox isolation**: `--sandbox` uses dedicated `NAMECHEAP_SANDBOX_*` credentials, separate credential files, and separate lock/backup/journal directories. Domain purchases, renewals, transfers, and DNSSEC mutations are outside this tool's scope.
