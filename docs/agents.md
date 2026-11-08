# Using Namecheap CLI from an agent

Use the installed `namecheap` command. Run `namecheap schema` to discover its
arguments and JSON contracts. You do not need an MCP server or custom API code.

Before starting, the user must provide API access and a domain allowlist. The
credential can reach the whole Namecheap account; the CLI allowlist is a local
guard, not provider-side access control. Do not expand the list without the
user's authorization.

For an edit:

1. Run `dns list DOMAIN` or `ns list DOMAIN` and inspect the state.
2. Run the intended mutation without `--yes`.
3. Read the diff and warnings. Check that the result matches the user's request.
4. If the change is authorized, repeat the same mutation with
   `--yes --if-match HASH`, using `data.state_hash` from the preview.
5. Check the exit code and JSON. A confirmed change needs `data.verified: true`.
   A successful no-op has `data.changed: false` and makes no write.

A preview's `ok: true` does not mean a write happened. The state hash is an
optimistic concurrency token for the observed state and environment. It does
not authorize a change or bind the token to particular edit parameters. Review
a fresh preview if you change the edit.

Pass arguments as an array through your subprocess tool. Do not build a shell
command from DNS values. TXT records often contain quotes and shell characters.
All commands return one JSON document on stdout. Help and --version return text.

Example in Python, assuming the user has authorized the A-record change:

```python
import json
import subprocess


def run(*args):
    completed = subprocess.run(["namecheap", *args], capture_output=True, text=True, check=False)
    result = json.loads(completed.stdout)
    if completed.returncode:
        raise RuntimeError(result["error"])
    return result


edit = ("dns", "set", "example.com", "@", "A", "203.0.113.10")
plan = run(*edit)
# Review plan["data"]["diff"] and plan["warnings"] before applying.
result = run(*edit, "--yes", "--if-match", plan["data"]["state_hash"])
assert result["data"]["verified"] or not result["data"]["changed"]
```

For a batch edit, write `dns list`'s `data.zone` to a JSON file and modify that
zone object. Preserve the domain and email_type. Preview it with `dns apply`.
The full CLI envelope is not a zone file.

| Error code | Next step |
|---|---|
| `out_of_scope` | Stop; obtain domain access through the user's normal process. |
| `stale_state` | Read and preview again. Reconcile the outside change. |
| `busy` | Another local process owns the write lock. Retry after it finishes. |
| `email_mode_unknown` | Check mail settings; do not invent an email mode. |
| `unsupported_metadata` / `unsupported_record` | Use the dashboard or another provider-supported route. |
| `write_unverified` | Inspect current state and the recovery journal. Never blindly retry. |
| `credentials_missing` | Supply credentials securely; never request a key in a prompt. |

Keep all local writers on the same state directory. The lock does not coordinate
different machines or the dashboard. API verification checks provider state,
not public DNS propagation or recursive caches.
