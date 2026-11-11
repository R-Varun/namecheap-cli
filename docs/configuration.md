# Configuration

Initialize configuration and your domain allowlist with:

```sh
namecheap config init --domain example.com
```

This writes `~/.config/namecheap-cli/config.json`:

```json
{
  "schema_version": 1,
  "allowed_domains": ["example.com"]
}
```

Add or remove authorized domains at any time:

```sh
namecheap config allow example.net
namecheap config disallow example.net
namecheap config show
```

The allowlist governs all per-domain reads and writes (`dns *`, `ns *`,
`restore`). `domains check` can inspect unowned domain availability, and
`domains list --all` explicitly lists all domains on the account. Note that the
allowlist is a local guardrail against accidental cross-domain edits—it is not a
security boundary against untrusted code that already has file or environment
access to your API key.

## Environment Variables & Paths

| Setting | Purpose |
|---|---|
| `--config PATH` / `NAMECHEAP_CONFIG_FILE` | Explicit configuration file path (`--config` takes precedence). |
| `NAMECHEAP_API_USER` / `NAMECHEAP_API_KEY` | Production API credentials. |
| `NAMECHEAP_USERNAME` | Optional production target username (defaults to `NAMECHEAP_API_USER`). |
| `NAMECHEAP_SANDBOX_API_USER` / `NAMECHEAP_SANDBOX_API_KEY` | Sandbox API credentials (used only with `--sandbox`). |
| `NAMECHEAP_SANDBOX_USERNAME` | Optional sandbox target username. |
| `NAMECHEAP_CLIENT_IP` | Explicit whitelisted public IPv4 address sent as `ClientIp`. |
| `NAMECHEAP_STATE_DIR` | Root directory for process locks, `0600` backups, and journals. |
| `XDG_CONFIG_HOME` / `XDG_STATE_HOME` | Base XDG directories on macOS and Linux. |

Default paths:
- **macOS / Linux**: `~/.config/namecheap-cli/config.json` and `~/.local/state/namecheap-cli/`
- **Windows**: `%APPDATA%\namecheap-cli\config.json` and `%LOCALAPPDATA%\namecheap-cli\`

All concurrent CLI processes operating on the same account should share the same
local `NAMECHEAP_STATE_DIR` (POSIX/Windows file locks are not guaranteed across
network filesystems).

## Credential Storage & Transport Hardening

- `namecheap auth setup --username USER` writes `credentials-production.json` (or `credentials-sandbox.json` with `--sandbox`) atomically with `0600` permissions on POSIX.
- Environment credentials override stored files, and setting only one half of an environment pair (`USER` without `KEY`, or vice versa) raises `credentials_incomplete` rather than silently mixing sources.
- Production credentials are never used as a fallback when `--sandbox` is set.
- The CLI never auto-loads `.env` files from working directories and exposes no `--api-key` flag that could leak into shell history or `ps`.
- Endpoints are pinned to Namecheap's HTTPS production/sandbox hosts and Cloudflare's HTTPS DoH endpoint (`1.1.1.1` / `cloudflare-dns.com` for `dns check`). HTTP redirects are refused, credentials travel in POST request bodies rather than URL query strings, and secrets are redacted from error messages.

## Public IPv4 Detection & Rate Spacing

Namecheap's API requires a whitelisted public IPv4 `ClientIp`. When
`NAMECHEAP_CLIENT_IP` is unset, the CLI queries the API host's `/cdn-cgi/trace`
endpoint over HTTPS and rejects IPv6 addresses. If your network routes dual-stack
traffic over IPv6 to Cloudflare, set `NAMECHEAP_CLIENT_IP` to your whitelisted
egress IPv4.

Requests within a single CLI invocation are spaced by at least 1.25 seconds for
Namecheap XML calls. Keep provider-wide rate limits in mind if running many
parallel read workers across one account.
