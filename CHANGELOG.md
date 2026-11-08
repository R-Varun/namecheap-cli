# Changelog

## 0.1.0

First public beta. BasicDNS records, nameservers, account domain listing, and
availability checks for agents and scripts.

- JSON results and errors, schema discovery, and an optional human view.
- Domain allowlists and separate production/sandbox credentials.
- Dry runs, required state hashes, and local process locks.
- Complete record verification, private backups, and recovery journals.
- Tests for concurrent writers, stale state, partial failures, malformed input,
  credential redaction, and TXT preservation.

Authenticated sandbox/live writes remain unverified. Unknown email metadata,
unsupported records, and managed settings block record writes.
