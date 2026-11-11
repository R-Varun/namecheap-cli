---
name: namecheap-dns
description: Manage Namecheap BasicDNS records, nameserver delegation, domain availability, and live public DNS propagation safely with the `namecheap` CLI. Use whenever the user asks to inspect, add, update, delete, apply, restore, or check propagation of DNS records or nameservers on Namecheap domains.
---

# Namecheap DNS CLI (`namecheap`)

Use the installed `namecheap` CLI (`namecheap-agent-cli`) to inspect and update Namecheap BasicDNS records and nameserver delegation safely with structured JSON output, dry runs, optimistic state-hash locking, automatic backups, and live DNS-over-HTTPS propagation checks.

## Core Safety Rules

1. **Discover first**: Run `namecheap schema` if you need exact command flags, zone JSON schema, or error codes.
2. **Never pass secrets in arguments or prompts**: Credentials come from `NAMECHEAP_API_USER` + `NAMECHEAP_API_KEY` (or `--sandbox` with `NAMECHEAP_SANDBOX_API_USER` + `NAMECHEAP_SANDBOX_API_KEY`) or `~/.config/namecheap-cli/credentials-*.json` (`chmod 600`) created interactively by the user via `namecheap auth setup`.
3. **Respect the local domain allowlist**: Per-domain commands only operate on allowlisted domains (`namecheap config show`). Never run `namecheap config allow <domain>` unless the user explicitly authorizes managing that domain.
4. **Always preview before writing**:
   - Every mutation (`dns add`, `dns set`, `dns remove`, `dns apply`, `ns set`, `restore`) is a **read-only dry run** unless `--yes --if-match <STATE_HASH>` is passed.
   - Inspect `data.diff`, `warnings`, and `data.state_hash` from the preview, confirm the change matches user intent, and only then apply with `--yes --if-match <STATE_HASH>`.
5. **Pass argv arrays, not shell-interpolated strings**: TXT records (SPF, DKIM, DMARC, ACME challenges) frequently contain quotes, semicolons, and spaces. Invoke `namecheap` as a subprocess argument array or pipe zone JSON via `namecheap dns apply <domain> -`.
6. **Never blindly retry `write_unverified` (exit code 4)**: Inspect `error.details.outcome` (`"rejected"` vs `"unknown"`), run `namecheap dns list <domain>`, and check `error.details.recovery.backup_file` before retrying or restoring.

## Standard Workflow

### 1. Preflight & Inspection

```sh
namecheap doctor
namecheap dns list example.com
namecheap ns list example.com
```

### 2. Preview -> Confirm -> Apply

```sh
# 1. Dry-run preview (no write occurs)
namecheap dns set example.com @ A 203.0.113.10

# 2. Apply using the 64-char SHA-256 state_hash from step 1
namecheap dns set example.com @ A 203.0.113.10 --yes --if-match <STATE_HASH>
```

- `dns set` replaces existing records of the same type at that host plus conflicting `CNAME`/`ALIAS`/redirect records.
- `dns add` appends a record when multiple values coexist (`A`, `AAAA`, `MX`, `TXT`, `NS`); identical existing records are a safe no-op.
- `dns remove example.com <host> <type> [--value <val>]` deletes matching records.
- `dns apply example.com -` reads a complete `{"domain": ..., "email_type": ..., "hosts": [...]}` zone JSON object from `stdin` (or pass a file path) for atomic multi-record edits.
- Changing mail routing (`FWD`, `MX`, `MXE`, `OX`) requires an explicit `--email-type` flag.

### 3. Verify Live Public DNS Propagation

`verified: true` on a write confirms Namecheap's API stored the zone. To check whether public DNS resolvers are serving the new records:

```sh
namecheap dns check example.com
namecheap dns check example.com --host _acme-challenge --type TXT
```

### 4. Rollback / Recovery

Every confirmed write saves a private `0600` backup before submission (`data.recovery.backup_file`):

```sh
namecheap restore /path/to/backup.json
namecheap restore /path/to/backup.json --yes --if-match <STATE_HASH>
```

## Error Handling Quick Reference

| Error Code | Exit | Meaning & Agent Action |
|---|---|---|
| `out_of_scope` | `3` | Domain not in allowlist. Ask the user before running `config allow`. |
| `stale_state` | `3` | Zone changed since preview. Re-run `dns list` / preview and reconcile. |
| `busy` | `3` | Another local CLI process holds the domain lock. Wait briefly and retry. |
| `external_dns` | `3` | Domain uses custom/third-party nameservers, not Namecheap BasicDNS. |
| `email_mode_unknown` | `3` | Ask user which `--email-type` (`MX`, `MXE`, `FWD`, `OX`) applies. |
| `unsupported_metadata` / `unsupported_record` | `3` | Zone has DDNS/inactive/SRV/CAA settings the CLI refuses to clobber; use dashboard. |
| `write_unverified` | `4` | Inspect `dns list` and `error.details.recovery` before retrying. |
| `credentials_missing` | `66` | Configure `NAMECHEAP_API_USER` and `NAMECHEAP_API_KEY` via environment/secret store. |
