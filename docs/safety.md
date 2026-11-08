# How writes work

Every DNS or nameserver edit is a dry run until you pass both `--yes` and the
`--if-match` state hash returned by its preview. The hash binds the change to
the observed state and environment. It is a conflict check, not a credential.

The CLI locks the account/environment/domain, reads the current state, computes
the edit, checks the preview hash, saves the old state, reads again to detect
outside changes, writes once, and checks the result. The lock covers the whole
transaction. A pending journal and backup exist before submission. Provider
errors, timeouts, and read-back failures after submission include recovery
paths and an outcome field. Writes are never retried automatically.

Namecheap setHosts replaces every host record. A lock coordinates CLI processes
that share the same local state directory. Other machines and the dashboard do
not share it. A final re-read narrows that race; it cannot eliminate it because
the provider API used here has no atomic compare-and-swap. Avoid other DNS
writers while a change is running.

TTL, MX preference, record multiplicity, and email mode participate in the
comparison. Hostname case and trailing dots are equivalent. TXT content is
preserved. Unknown record types or provider-managed host settings block record
writes rather than being silently discarded.

Email handling follows the provider's supported modes. Missing metadata never
means 'no email'. The CLI blocks a write if it cannot preserve the current email
mode. An explicit --email-type is an intentional email-setting change; review
the preview. Turning email off through an assumed omitted parameter is not
supported. The first release is tested offline, with provider behavior not yet
validated by an authenticated sandbox/live write.

Use the provider sandbox for integration validation. Sandbox credentials and
state are separate from production. Registration, renewal, transfers, purchases,
and DNSSEC changes are outside this tool's scope.
