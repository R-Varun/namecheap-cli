# Using Namecheap CLI from an agent

Use the installed `namecheap` binary directly—no MCP server or custom XML wrapper
is required. Run `namecheap schema` at any time to inspect the full command
catalogue, conditional argument rules, zone JSON schema, and error-code metadata.

If your agent supports portable skills, install [`skills/namecheap-dns/SKILL.md`](../skills/namecheap-dns/SKILL.md)
into `~/.claude/skills/namecheap-dns/` or `~/.agents/skills/namecheap-dns/`.

## Authorization & Scope

Before running domain commands, ensure the user has configured API credentials
(`NAMECHEAP_API_USER` + `NAMECHEAP_API_KEY`, or `namecheap auth setup`) and a
domain allowlist (`namecheap config show`). Because a Namecheap API key grants
account-wide authority at the provider, the local allowlist prevents accidental
cross-domain edits. Never run `namecheap config allow` or `config disallow`
without explicit user instruction.

## Standard Mutation Workflow

1. Run `namecheap dns list DOMAIN` or `namecheap ns list DOMAIN` to inspect current state.
2. Run the intended mutation **without** `--yes` to generate a dry-run preview.
3. Inspect `data.diff`, `warnings`, and `data.state_hash`. Confirm the diff matches the user's intent.
4. Execute the exact same mutation with `--yes --if-match <data.state_hash>`.
5. Check exit code `0` and `data.verified == true` (confirmed no-ops also return `verified: true` with `changed: false` and `outcome: "noop"`).
6. Optionally run `namecheap dns check DOMAIN [--host HOST] [--type TYPE]` to verify public DNS-over-HTTPS propagation.

A preview's `ok: true` means the preview succeeded—no write has occurred yet.
`state_hash` is an optimistic concurrency token for the observed zone and
environment; if you alter the intended edit, run a fresh preview first.

## Subprocess Best Practices

Always pass arguments as an `argv` list rather than a shell-interpolated string.
TXT records (SPF, DKIM, DMARC, ACME challenges) routinely contain quotes,
semicolons, and spaces. Every normal invocation emits exactly one JSON document
on `stdout`.

```python
import json
import subprocess


def namecheap(*args: str, stdin_text: str | None = None) -> dict:
    proc = subprocess.run(
        ["namecheap", *args],
        input=stdin_text,
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(proc.stdout)
    if proc.returncode != 0:
        raise RuntimeError(payload["error"])
    return payload


edit = ("dns", "set", "example.com", "@", "A", "203.0.113.10")
plan = namecheap(*edit)
# Inspect plan["data"]["diff"] and plan["warnings"] before confirming.
applied = namecheap(*edit, "--yes", "--if-match", plan["data"]["state_hash"])
assert applied["data"]["verified"] is True
```

## Atomic Batch Edits via `stdin`

When changing several records together, read `data.zone` from `dns list`, edit
that zone dictionary (`{"domain": ..., "email_type": ..., "hosts": [...]}`), and
pipe it directly to `dns apply DOMAIN -` without temporary files:

```python
zone = namecheap("dns", "list", "example.com")["data"]["zone"]
zone["hosts"].append({"name": "api", "type": "A", "address": "203.0.113.25", "ttl": 1800})
zone_json = json.dumps(zone)

preview = namecheap("dns", "apply", "example.com", "-", stdin_text=zone_json)
applied = namecheap(
    "dns",
    "apply",
    "example.com",
    "-",
    "--yes",
    "--if-match",
    preview["data"]["state_hash"],
    stdin_text=zone_json,
)
```

Note: Pass the `data.zone` object (not the outer CLI response envelope) to `dns apply`.

## Error Handling Guide

| Error Code | Exit | Recommended Agent Action |
|---|---|---|
| `out_of_scope` | `3` | Stop and ask the user whether to allowlist the domain. |
| `stale_state` | `3` | Re-read `dns list` / `ns list`, generate a fresh preview, and reconcile outside edits. |
| `busy` | `3` | Another local CLI process holds the domain lock; wait briefly and retry. |
| `external_dns` | `3` | Domain is delegated to custom nameservers (`ns list`), so BasicDNS records are inactive. |
| `email_mode_unknown` | `3` | Ask the user which `--email-type` (`MX`, `MXE`, `FWD`, `OX`) should be set. |
| `unsupported_metadata` / `unsupported_record` | `3` | Zone uses dashboard-managed features (e.g. DDNS/SRV/CAA); stop rather than clobbering. |
| `write_unverified` | `4` | Check `error.details.outcome` (`"rejected"` vs `"unknown"`), inspect `dns list`, and review `error.details.recovery` before retrying or running `restore`. |
| `credentials_missing` | `66` | Prompt the user to configure environment secrets or run `namecheap auth setup` themselves. |

Keep all local writers pointed at the same `NAMECHEAP_STATE_DIR`. API
verification proves Namecheap stored the zone; `dns check` verifies what public
DNS resolvers currently answer (which may lag behind TTL caches).
