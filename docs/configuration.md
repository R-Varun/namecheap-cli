# Configuration

Run `namecheap config init --domain example.com` to create:

```json
{
  "schema_version": 1,
  "allowed_domains": ["example.com"]
}
```

The allowlist applies to per-domain reads and writes. Availability checks can
check unowned names. `domains list --all` explicitly reads the whole account.
Anyone who can edit configuration or use the key directly can bypass the list;
it is not a sandbox for an untrusted process.

| Setting | Purpose |
|---|---|
| `--config PATH` / `NAMECHEAP_CONFIG_FILE` | Configuration file; the flag wins. |
| `NAMECHEAP_API_USER` / `NAMECHEAP_API_KEY` | Production credentials. |
| `NAMECHEAP_USERNAME` | Optional target account; defaults to API user. |
| `NAMECHEAP_SANDBOX_API_USER` / `NAMECHEAP_SANDBOX_API_KEY` | Sandbox credentials. |
| `NAMECHEAP_SANDBOX_USERNAME` | Optional sandbox target account. |
| `NAMECHEAP_CLIENT_IP` | Explicit whitelisted public IPv4. |
| `NAMECHEAP_STATE_DIR` | Shared local root for locks, backups, and journals. |
| `XDG_CONFIG_HOME` / `XDG_STATE_HOME` | Base directories on macOS/Linux. |

Default configuration is `~/.config/namecheap-cli/config.json` and state is
`~/.local/state/namecheap-cli`. Windows uses `%APPDATA%` and `%LOCALAPPDATA%`.
Writers for the same account should share a local state root. Network-filesystem
lock semantics are not guaranteed.

`auth setup` stores `credentials-production.json` or
`credentials-sandbox.json` next to configuration. Files are written atomically,
with mode 0600 on POSIX. Environment credentials take precedence; an incomplete
pair is an error rather than a fallback. The tool does not evaluate shell dotenv
files and has no API-key command-line flag. On Windows, protect the containing
directory using your normal user ACLs.

There is no arbitrary endpoint override. `--sandbox` selects the fixed HTTPS
sandbox endpoint. Redirects are refused. Credentials go in POST bodies, never
URLs. Provider errors are redacted before output.

IP detection uses the API host's Cloudflare trace page. IPv6 responses are
refused because Namecheap's whitelist is IPv4. If detection fails, set
`NAMECHEAP_CLIENT_IP` to your whitelisted public IPv4. This does not change the
network route: requests must actually leave from that address.

Calls within one client are spaced by at least 1.25 seconds. There is no global
cross-process quota scheduler. Respect account-wide provider limits when running
many agents. The CLI never retries a write on its own.
