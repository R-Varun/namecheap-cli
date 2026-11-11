# Security Policy

## Reporting a Vulnerability

Never post API keys, account usernames, whitelisted client IPs, DNS backup
files, or runtime journals in public issues or pull requests.

Please report security vulnerabilities privately through
[GitHub Private Vulnerability Reporting](https://github.com/R-Varun/namecheap-cli/security/advisories/new).
Non-sensitive bug reports and feature requests are welcome in public GitHub
Issues.

## Security Model

- **Account-wide API authority**: Namecheap API keys have account-wide privileges at the provider. The CLI's `allowed_domains` list prevents accidental cross-domain operations by agents and scripts, not malicious actions by an untrusted process that can read your credential file or environment variables.
- **Credential handling**: Credentials are supplied via environment variables (`NAMECHEAP_API_USER` / `NAMECHEAP_API_KEY`) or stored in `0600` local JSON files via `namecheap auth setup`. Secrets are sent in HTTPS POST bodies, never accepted on the command line, and automatically redacted from error output.
- **Sandbox isolation**: `--sandbox` enforces separate credentials (`NAMECHEAP_SANDBOX_*`) and separate state/backup directories with zero fallback to production keys.

See [docs/safety.md](docs/safety.md) and [docs/configuration.md](docs/configuration.md)
for full details on write locking, backups, recovery journals, and transport rules.
