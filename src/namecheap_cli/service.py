"""DNS transactions shared by the CLI and Python callers."""

import hashlib
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .client import Client
from .config import Config, domain_name
from .errors import CliError
from .records import (
    TYPES,
    Host,
    Zone,
    differences,
    edit,
    fingerprint,
    make_host,
    relative_name,
    settle_email,
    validate,
)
from .storage import atomic_json, domain_lock, read_json


class Service:
    def __init__(self, config: Config, client: Client, state_dir: Path) -> None:
        self.config = config
        self.client = client
        self.profile = client.profile
        account = f"{client.credentials.api_user}:{client.credentials.username}"
        self.account_id = hashlib.sha256(account.encode()).hexdigest()[:24]
        self.state_dir = state_dir / self.profile / self.account_id

    def dns_list(self, domain: str) -> dict:
        domain = self.config.require(domain)
        zone = self.client.hosts(domain)
        return {
            "zone": zone.to_json(),
            "state_hash": fingerprint(zone.canonical(), self.profile),
            "unsupported_metadata": list(zone.unsupported),
        }

    def ns_list(self, domain: str) -> dict:
        domain = self.config.require(domain)
        state = self.client.nameservers(domain)
        return {**state, "state_hash": fingerprint(state, self.profile)}

    def dns_edit(
        self,
        domain: str,
        action: str,
        *,
        name: str = "@",
        rtype: str = "A",
        value: str | None = None,
        ttl: int = 1800,
        mxpref: int = 10,
        zone_file: Path | None = None,
        email_type: str | None = None,
        yes: bool = False,
        if_match: str | None = None,
    ) -> dict:
        domain = self.config.require(domain)
        if action == "apply":
            if zone_file is None:
                raise CliError("invalid_file", "A zone file is required.")
            file_zone = Zone.from_json(domain, read_json(zone_file))
        else:
            file_zone = None
        # Validate supplied values before network access.
        if action == "remove" and value is None:
            if rtype.upper() not in TYPES:
                raise CliError("invalid_record", "Unsupported record type.")
            host = Host(relative_name(name, domain), rtype.upper(), "")
        elif action != "apply":
            host = make_host(domain, name, rtype, value, ttl, mxpref)
        else:
            host = None

        def change(old: Zone) -> tuple[Zone, list[str]]:
            # Protect the source state even if a replacement file would erase
            # unsupported provider-managed attributes.
            if old.unsupported:
                raise CliError(
                    "unsupported_metadata",
                    "Cannot preserve provider-managed host settings.",
                    exit_code=3,
                    details={"fields": list(old.unsupported)},
                )
            for existing in old.hosts:
                if existing.type not in TYPES:
                    raise CliError(
                        "unsupported_record",
                        f"Cannot safely rewrite record type {existing.type}.",
                        exit_code=3,
                    )
            new = (
                file_zone
                if file_zone is not None
                else edit(old, action, host, match_value=value is not None)
            )
            override = email_type
            if file_zone is not None and override is None:
                override = file_zone.email_type
            new, warnings = settle_email(old, new, override)
            validate(new)
            return new, warnings

        return self._transaction(
            domain,
            "dns",
            self.client.hosts,
            change,
            lambda z: z.canonical(),
            lambda z: z.to_json(),
            lambda old, new: differences(old, new),
            self.client.set_hosts,
            lambda want, have: want.canonical() == have.canonical(),
            yes,
            if_match,
        )

    def ns_set(
        self,
        domain: str,
        *,
        servers: list[str] | None = None,
        default: bool = False,
        yes: bool = False,
        if_match: str | None = None,
    ) -> dict:
        domain = self.config.require(domain)
        if default == (servers is not None):
            raise CliError(
                "invalid_nameservers", "Choose either --default or --custom nameservers."
            )
        normalized = sorted(domain_name(server) for server in (servers or []))
        if not default and (
            not 2 <= len(normalized) <= 12 or len(set(normalized)) != len(normalized)
        ):
            raise CliError("invalid_nameservers", "Provide 2–12 distinct nameservers.")

        def change(old: dict) -> tuple[dict, list[str]]:
            target = {
                "domain": domain,
                "mode": "namecheap" if default else "custom",
                "nameservers": old["nameservers"]
                if default and old["mode"] == "namecheap"
                else normalized,
            }
            warnings = [
                "Prepare all records at the target DNS provider before changing nameservers.",
                "A nameserver backup restores delegation only; check DNS records separately.",
            ]
            return target, warnings

        def matches(want: dict, have: dict) -> bool:
            return want["mode"] == have["mode"] and (
                want["mode"] == "namecheap" or want["nameservers"] == have["nameservers"]
            )

        return self._transaction(
            domain,
            "ns",
            self.client.nameservers,
            change,
            lambda x: x,
            lambda x: x,
            lambda old, new: (
                []
                if matches(new, old)
                else [{"action": "nameservers", "before": old, "after": new}]
            ),
            self.client.set_nameservers,
            matches,
            yes,
            if_match,
        )

    def restore(self, file: Path, *, yes: bool = False, if_match: str | None = None) -> dict:
        backup = read_json(file)
        if (
            not isinstance(backup, dict)
            or backup.get("schema_version") != 1
            or backup.get("kind") not in {"dns", "ns"}
            or backup.get("profile") != self.profile
            or backup.get("account_id") != self.account_id
            or not isinstance(backup.get("before"), dict)
        ):
            raise CliError(
                "invalid_backup", "Backup must match this CLI version, environment, and account."
            )
        before = backup["before"]
        domain = self.config.require(before.get("domain", ""))
        if backup["kind"] == "ns":
            if before.get("mode") not in {"namecheap", "custom"} or not isinstance(
                before.get("nameservers"), list
            ):
                raise CliError("invalid_backup", "Malformed nameserver backup.")
            return self.ns_set(
                domain,
                default=before["mode"] == "namecheap",
                servers=before["nameservers"] if before["mode"] == "custom" else None,
                yes=yes,
                if_match=if_match,
            )
        restored = Zone.from_json(domain, before)
        if restored.email_type is None:
            raise CliError(
                "email_mode_unknown",
                "This backup has no known email mode; automatic restoration cannot preserve it.",
                exit_code=3,
            )

        def change(old: Zone) -> tuple[Zone, list[str]]:
            validate(old)
            new, warnings = settle_email(old, restored, restored.email_type)
            validate(new)
            return new, warnings

        return self._transaction(
            domain,
            "dns",
            self.client.hosts,
            change,
            lambda z: z.canonical(),
            lambda z: z.to_json(),
            differences,
            self.client.set_hosts,
            lambda a, b: a.canonical() == b.canonical(),
            yes,
            if_match,
        )

    def _transaction(
        self,
        domain: str,
        kind: str,
        read: Callable,
        change: Callable,
        canonical: Callable,
        serialize: Callable,
        diff: Callable,
        write: Callable,
        matches: Callable,
        yes: bool,
        if_match: str | None,
    ) -> dict:
        if yes and not if_match:
            raise CliError(
                "preview_required", "Preview first, then pass --yes --if-match STATE_HASH."
            )
        if if_match and (len(if_match) != 64 or any(c not in "0123456789abcdef" for c in if_match)):
            raise CliError(
                "invalid_state_hash", "Expected a 64-character lowercase SHA-256 state hash."
            )
        with domain_lock(self.state_dir / "locks" / f"{domain}.lock"):
            old = read(domain)
            current_hash = fingerprint(canonical(old), self.profile)
            if if_match is not None and current_hash != if_match:
                raise CliError(
                    "stale_state",
                    "The domain changed since the preview. Preview again.",
                    exit_code=3,
                    details={"current_state_hash": current_hash},
                )
            new, warnings = change(old)
            changes = diff(old, new)
            plan = {
                "domain": domain,
                "kind": kind,
                "profile": self.profile,
                "dry_run": not yes,
                "changed": bool(changes),
                "state_hash": current_hash,
                "before": serialize(old),
                "after": serialize(new),
                "diff": changes,
                "verified": False,
                "warnings": warnings,
            }
            if not changes or not yes:
                return plan
            token = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex
            backup = self.state_dir / "backups" / domain / f"{token}.json"
            journal = self.state_dir / "journals" / domain / f"{token}.json"
            saved = {
                "schema_version": 1,
                "kind": kind,
                "profile": self.profile,
                "account_id": self.account_id,
                "created_at": datetime.now(UTC).isoformat(),
                "before": serialize(old),
                "after": serialize(new),
            }
            atomic_json(backup, saved, replace=False)
            recovery = {
                "backup_file": str(backup),
                "journal_file": str(journal),
                "restore_preview_argv": [
                    "namecheap",
                    "--config",
                    str(self.config.path),
                    *(["--sandbox"] if self.profile == "sandbox" else []),
                    "restore",
                    str(backup),
                ],
            }
            entry = {**saved, "status": "prepared", "recovery": recovery}
            atomic_json(journal, entry, replace=False)
            # A best-effort external-writer check. The API has no atomic CAS.
            fresh = read(domain)
            if canonical(fresh) != canonical(old):
                entry["status"] = "conflict"
                atomic_json(journal, entry)
                raise CliError(
                    "stale_state",
                    "The domain changed before submission. No write was sent.",
                    exit_code=3,
                    details={"recovery": recovery},
                )
            entry["status"] = "submitted"
            atomic_json(journal, entry)
            try:
                write(domain, new)
                actual = read(domain)
                if not matches(new, actual):
                    raise CliError(
                        "verification_mismatch",
                        "Provider state differs from the requested state.",
                        details={"actual": serialize(actual), "expected": serialize(new)},
                    )
                entry["status"] = "verified"
                atomic_json(journal, entry)
            except (Exception, KeyboardInterrupt) as exc:
                interrupted = isinstance(exc, KeyboardInterrupt)
                reason = (
                    exc.to_json()
                    if isinstance(exc, CliError)
                    else {"exception": type(exc).__name__}
                )
                entry.update(status="unverified", error=reason)
                try:
                    atomic_json(journal, entry)
                except OSError:
                    pass  # The durable submitted journal still points to the backup.
                raise CliError(
                    "write_unverified",
                    "A write was submitted but could not be verified. "
                    "Inspect current state before retrying or restoring.",
                    exit_code=130 if interrupted else 4,
                    details={"outcome": "unknown", "cause": reason, "recovery": recovery},
                ) from None
            return {
                **plan,
                "verified": True,
                "outcome": "verified",
                "recovery": recovery,
                "after": serialize(actual),
                "state_hash": fingerprint(canonical(actual), self.profile),
            }
