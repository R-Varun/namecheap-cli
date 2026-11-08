# Security

Do not post API keys, private account data, backups, or credential files in an
issue. Report vulnerabilities through this repository's private vulnerability
reporting feature if enabled, or contact the owner through the GitHub profile
before sharing sensitive details. Public issues are fine for non-sensitive bugs.

The API key can control the whole account. The allowlist reduces accidents;
it does not restrict a process that already holds the key. Use a trusted agent
environment and the provider sandbox when testing.

This beta has automated offline coverage. Authenticated provider writes have
not been verified. See docs/safety.md for the write lifecycle, recovery behavior,
unsupported settings, and remaining race with other DNS writers.
