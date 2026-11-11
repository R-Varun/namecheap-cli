# Changelog

## 0.2.0

Production-ready release expanding agent ergonomics, live DNS verification,
strict input validation, and documentation/discovery surfaces.

- **Live DNS propagation checks**: Added `namecheap dns check <domain> [--host HOST] [--type TYPE]` using zero-dependency Cloudflare DNS-over-HTTPS (`resolve_doh`).
- **Stdin batch zone apply**: `namecheap dns apply <domain> -` now accepts bounded UTF-8 zone JSON directly on `stdin` for temp-file-free agent pipelines.
- **Allowlist management**: Added `namecheap config disallow <domains...>` alongside `config allow`.
- **Portable Agent Skill & LLM discovery**: Added `skills/namecheap-dns/SKILL.md` (included in sdist), `llms.txt`, and live `schema.json` publication on GitHub Pages.
- **Richer `namecheap schema` contract**: Added `repeatable`, `mutually_exclusive_groups`, conditional `mxpref` validation rules, and a machine-readable `error_codes` catalogue.
- **Write-transaction & validation hardening**:
  - Confirmed no-op writes (`--yes --if-match` with empty diff) now consistently return `verified: true` and `outcome: "noop"`.
  - Pre-submission re-read failures now mark the journal `"aborted"` and attach `recovery` paths; provider `setHosts` rejections report `outcome: "rejected"` distinct from `"unknown"` transport failures.
  - Disabled `argparse` prefix abbreviations (`allow_abbrev=False`), moved local record/host/domain validation ahead of credential and IP lookups, rejected scoped IPv6 zone IDs on `AAAA`, tightened per-label host syntax and redirect URL port/userinfo validation, and rejected `mxpref` on non-`MX` records in `dns apply`.

## 0.1.0

Initial public release of `namecheap-agent-cli` for Namecheap BasicDNS records,
nameserver delegation, account domain listing, and availability checks.

- Versioned JSON output envelope (`schema_version: 1`), `namecheap schema` discovery, and `--human` terminal formatting.
- Local domain allowlists and strict production/sandbox credential isolation.
- Dry-run previews by default, `--if-match` SHA-256 optimistic concurrency, and local per-domain process locks.
- Post-write record/TTL/MX/email verification, `0600` pre-write backups, and `namecheap restore`.
