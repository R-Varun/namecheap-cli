"""Command line contract: one versioned JSON result, including errors."""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .client import Client, detect_ipv4, ipv4, resolve_doh
from .config import Config, Credentials, config_path, domain_name, setup_credentials, state_path
from .errors import CliError
from .records import DEFAULT_MXPREF, EMAIL_TYPES, TYPES, Zone, make_host, relative_name
from .service import Service
from .storage import atomic_json, read_json, read_json_stdin


class Parser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def error(self, message):
        raise CliError("usage", message)


def build_parser() -> Parser:
    common = Parser(add_help=False)
    common.add_argument(
        "--config", type=Path, default=argparse.SUPPRESS, help="configuration JSON path"
    )
    common.add_argument(
        "--sandbox",
        action="store_true",
        default=argparse.SUPPRESS,
        help="use separate Namecheap sandbox credentials and state",
    )
    output = common.add_mutually_exclusive_group()
    output.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS, help="JSON output (default)"
    )
    output.add_argument(
        "--human", action="store_true", default=argparse.SUPPRESS, help="short readable output"
    )
    parser = Parser(
        prog="namecheap",
        parents=[common],
        description="Namecheap DNS for agents and scripts. JSON by default; edits are dry runs.",
    )
    parser.add_argument("--version", action="version", version=f"namecheap {__version__}")
    groups = parser.add_subparsers(dest="group", required=True)

    def group(name, help_text):
        parent = groups.add_parser(name, help=help_text, parents=[common])
        return parent.add_subparsers(dest="action", required=True)

    def command(parent, name, help_text):
        return parent.add_parser(name, help=help_text, description=help_text, parents=[common])

    def write(p):
        p.add_argument("--yes", action="store_true", help="apply after reviewing the dry run")
        p.add_argument(
            "--if-match",
            metavar="STATE_HASH",
            help="state_hash returned by the preview; required with --yes",
        )

    def email(p):
        p.add_argument(
            "--email-type",
            choices=EMAIL_TYPES,
            type=str.upper,
            help="explicit email-mode change; omitted means preserve",
        )

    config = group("config", "manage the local domain allowlist")
    p = command(config, "init", "create a configuration with allowed domains")
    p.add_argument("--domain", action="append", default=[], help="allow a domain (repeatable)")
    p = command(config, "allow", "allow one or more domains you are authorized to manage")
    p.add_argument("domains", nargs="+")
    p = command(config, "disallow", "remove one or more domains from the local allowlist")
    p.add_argument("domains", nargs="+")
    command(config, "show", "show non-secret configuration")
    auth = group("auth", "store credentials from a hidden prompt")
    p = command(
        auth, "setup", "store credentials locally; agents can use environment variables instead"
    )
    p.add_argument("--username", required=True)
    p.add_argument("--force", action="store_true")
    command(groups, "doctor", "check configuration, IPv4, and read-only API access")
    command(groups, "schema", "describe commands, JSON output, error codes, and zone-file schemas")
    domains = group("domains", "list domains or check availability")
    p = command(domains, "list", "list allowed domains in the account")
    p.add_argument(
        "--all", action="store_true", help="explicitly include domains outside the allowlist"
    )
    p = command(domains, "check", "check registration availability; never registers a domain")
    p.add_argument("names", nargs="+")
    dns = group("dns", "read, verify, and edit BasicDNS records")
    p = command(dns, "list", "return the zone and its state hash")
    p.add_argument("domain")
    p = command(dns, "check", "compare Namecheap zone records against live public DNS")
    p.add_argument("domain")
    p.add_argument("--host", help="optional host filter (@, relative name, or FQDN)")
    p.add_argument("--type", dest="rtype", type=str.upper, choices=TYPES, help="record type filter")
    for action in ("add", "set", "remove"):
        p = command(
            dns,
            action,
            {
                "add": "add a record (identical existing records are a no-op)",
                "set": "replace records of this type at the host and any conflicting records",
                "remove": "remove matching records (absent records are a no-op)",
            }[action],
        )
        p.add_argument("domain")
        p.add_argument("host", help="@, a relative name, or a name within the domain")
        p.add_argument("type", type=str.upper, choices=TYPES)
        if action == "remove":
            p.add_argument(
                "--value", help="match one value; omitted removes all of this type at the host"
            )
        else:
            p.add_argument("value")
            p.add_argument("--ttl", type=int, default=1800)
            p.add_argument("--mxpref", type=int, default=None, help="MX priority (MX records only)")
        write(p)
        email(p)
    p = command(dns, "apply", "replace the whole zone from a zone JSON file or '-' for stdin")
    p.add_argument("domain")
    p.add_argument("file", help="path to zone JSON file, or '-' to read from standard input")
    write(p)
    email(p)
    ns = group("ns", "read and change nameserver delegation")
    p = command(ns, "list", "return nameservers and their state hash")
    p.add_argument("domain")
    p = command(ns, "set", "change nameservers; create target DNS records before applying")
    p.add_argument("domain")
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--custom", nargs="+", metavar="NS")
    target.add_argument("--default", action="store_true")
    write(p)
    p = command(groups, "restore", "preview or apply restoration of a saved DNS/nameserver backup")
    p.add_argument("file", type=Path)
    write(p)
    return parser


ERROR_CATALOGUE = {
    "usage": {"exit_code": 2, "retryable": False},
    "config_missing": {"exit_code": 2, "retryable": False},
    "invalid_config": {"exit_code": 2, "retryable": False},
    "invalid_domain": {"exit_code": 2, "retryable": False},
    "invalid_record": {"exit_code": 2, "retryable": False},
    "invalid_nameservers": {"exit_code": 2, "retryable": False},
    "invalid_file": {"exit_code": 2, "retryable": False},
    "invalid_backup": {"exit_code": 2, "retryable": False},
    "invalid_state_hash": {"exit_code": 2, "retryable": False},
    "preview_required": {"exit_code": 2, "retryable": False},
    "file_exists": {"exit_code": 2, "retryable": False},
    "unsafe_file": {"exit_code": 2, "retryable": False},
    "local_io_error": {"exit_code": 2, "retryable": False},
    "ipv4_required": {"exit_code": 2, "retryable": False},
    "terminal_required": {"exit_code": 2, "retryable": False},
    "invalid_username": {"exit_code": 2, "retryable": False},
    "invalid_key": {"exit_code": 2, "retryable": False},
    "unsafe_credentials": {"exit_code": 2, "retryable": False},
    "invalid_credentials": {"exit_code": 66, "retryable": False},
    "credentials_missing": {"exit_code": 66, "retryable": False},
    "credentials_incomplete": {"exit_code": 66, "retryable": False},
    "out_of_scope": {"exit_code": 3, "retryable": False},
    "external_dns": {"exit_code": 3, "retryable": False},
    "unsupported_metadata": {"exit_code": 3, "retryable": False},
    "unsupported_record": {"exit_code": 3, "retryable": False},
    "email_mode_unknown": {"exit_code": 3, "retryable": False},
    "stale_state": {"exit_code": 3, "retryable": False},
    "busy": {"exit_code": 3, "retryable": True},
    "ip_detection_failed": {"exit_code": 1, "retryable": True},
    "network_error": {"exit_code": 1, "retryable": True},
    "http_error": {"exit_code": 1, "retryable": True},
    "doh_error": {"exit_code": 1, "retryable": True},
    "provider_error": {"exit_code": 1, "retryable": False},
    "availability_failed": {"exit_code": 1, "retryable": False},
    "invalid_response": {"exit_code": 1, "retryable": False},
    "redirect_refused": {"exit_code": 1, "retryable": False},
    "write_rejected": {"exit_code": 1, "retryable": False},
    "write_unverified": {"exit_code": 4, "retryable": False},
    "interrupted": {"exit_code": 130, "retryable": False},
}


def schema(parser: argparse.ArgumentParser) -> dict:
    commands = []

    def walk(p, path):
        sub = next((a for a in p._actions if isinstance(a, argparse._SubParsersAction)), None)
        if sub is not None:
            for name, child in sub.choices.items():
                walk(child, [*path, name])
            return
        args = []
        for a in p._actions:
            if a.dest == "help":
                continue
            item = {
                "name": a.dest,
                "flags": a.option_strings,
                "required": a.required,
                "repeatable": isinstance(a, argparse._AppendAction),
                "help": a.help or "",
                "nargs": a.nargs,
                "type": "integer" if a.type is int else "boolean" if a.nargs == 0 else "string",
            }
            if a.choices is not None:
                item["choices"] = list(a.choices)
            args.append(item)
        mutex = [
            {
                "required": group_item.required,
                "flags": [
                    act.option_strings[0] for act in group_item._group_actions if act.option_strings
                ],
            }
            for group_item in p._mutually_exclusive_groups
            if group_item._group_actions
        ]
        commands.append(
            {
                "command": " ".join(path),
                "description": p.description,
                "arguments": args,
                "mutually_exclusive_groups": mutex,
            }
        )

    walk(parser, [])
    zone = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Namecheap zone",
        "type": "object",
        "required": ["domain", "hosts"],
        "additionalProperties": False,
        "properties": {
            "domain": {"type": "string"},
            "email_type": {"enum": [*EMAIL_TYPES, None]},
            "hosts": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["name", "type", "address"],
                    "additionalProperties": False,
                    "properties": {
                        "name": {"type": "string"},
                        "type": {"enum": list(TYPES)},
                        "address": {"type": "string", "minLength": 1},
                        "ttl": {"type": "integer", "minimum": 60, "maximum": 60000},
                        "mxpref": {"type": "integer", "minimum": 0, "maximum": 65535},
                    },
                    "allOf": [
                        {
                            "if": {"not": {"properties": {"type": {"const": "MX"}}}},
                            "then": {"not": {"required": ["mxpref"]}},
                        }
                    ],
                },
            },
        },
    }
    envelope = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Namecheap CLI result",
        "type": "object",
        "required": ["schema_version", "ok", "command"],
        "properties": {
            "schema_version": {"const": 1},
            "ok": {"type": "boolean"},
            "command": {"type": "string"},
            "data": {"type": "object"},
            "warnings": {"type": "array", "items": {"type": "string"}},
            "error": {
                "type": "object",
                "required": ["code", "message", "details", "retryable"],
                "properties": {
                    "code": {"type": "string"},
                    "message": {"type": "string"},
                    "details": {"type": "object"},
                    "retryable": {"type": "boolean"},
                },
            },
        },
        "allOf": [
            {
                "if": {"properties": {"ok": {"const": True}}},
                "then": {"required": ["data", "warnings"]},
                "else": {"required": ["error"]},
            }
        ],
    }
    return {
        "cli_version": __version__,
        "output_schema_version": 1,
        "commands": commands,
        "output_schema": envelope,
        "zone_file_schema": zone,
        "error_codes": ERROR_CATALOGUE,
        "exit_codes": {
            "0": "success",
            "1": "provider/network failure",
            "2": "invalid input",
            "3": "blocked or conflict",
            "4": "write unverified",
            "66": "missing credentials",
            "130": "interrupted",
        },
    }


def _validate_write_flags(yes: bool, if_match: str | None) -> None:
    if yes and not if_match:
        raise CliError("preview_required", "Preview first, then pass --yes --if-match STATE_HASH.")
    if if_match and (len(if_match) != 64 or any(c not in "0123456789abcdef" for c in if_match)):
        raise CliError(
            "invalid_state_hash", "Expected a 64-character lowercase SHA-256 state hash."
        )


def dispatch(
    args,
    env,
    secrets: list[str],
    client_factory,
    *,
    stdin: Any = None,
    doh_resolver=resolve_doh,
) -> dict:
    path = getattr(args, "config", config_path(env)).expanduser().absolute()
    profile = "sandbox" if getattr(args, "sandbox", False) else "production"
    if args.group == "schema":
        return schema(build_parser())
    if args.group == "config":
        if args.action == "init":
            config = Config(path, tuple(sorted({domain_name(n) for n in args.domain})))
            atomic_json(path, config.to_json(), replace=False)
        elif args.action == "allow":
            config = Config.load(path)
            config = Config(
                path,
                tuple(sorted(set(config.allowed_domains) | {domain_name(n) for n in args.domains})),
            )
            atomic_json(path, config.to_json())
        elif args.action == "disallow":
            config = Config.load(path)
            targets = {domain_name(n) for n in args.domains}
            removed = sorted(set(config.allowed_domains) & targets)
            config = Config(path, tuple(sorted(set(config.allowed_domains) - targets)))
            atomic_json(path, config.to_json())
            return {**config.to_json(), "removed": removed, "path": str(path)}
        else:
            config = Config.load(path)
        return {**config.to_json(), "path": str(path)}
    if args.group == "auth":
        file = setup_credentials(path, profile, args.username, force=args.force)
        return {"stored": str(file), "profile": profile}
    config = Config.load(path)
    # Scope and local input syntax are checked before credentials, IP detection, and network access.
    domain = config.require(args.domain) if hasattr(args, "domain") else None
    write = {"yes": getattr(args, "yes", False), "if_match": getattr(args, "if_match", None)}
    _validate_write_flags(**write)
    zone_data = None
    if args.group == "domains" and args.action == "check":
        checked_names = list(dict.fromkeys(domain_name(n) for n in args.names))
    else:
        checked_names = []
    if args.group == "restore":
        saved = read_json(args.file)
        if not isinstance(saved, dict) or not isinstance(saved.get("before"), dict):
            raise CliError("invalid_backup", "Expected a backup object with a before snapshot.")
        config.require(saved["before"].get("domain", ""))
    elif args.group == "ns" and args.action == "set":
        if args.custom is not None:
            normalized = sorted(domain_name(server) for server in args.custom)
            if not 2 <= len(normalized) <= 12 or len(set(normalized)) != len(normalized):
                raise CliError("invalid_nameservers", "Provide 2–12 distinct nameservers.")
    elif args.group == "dns" and args.action == "check":
        if args.host is not None:
            relative_name(args.host, domain)
    elif args.group == "dns" and args.action == "apply":
        zone_data = read_json_stdin(stdin) if args.file == "-" else read_json(Path(args.file))
        Zone.from_json(domain, zone_data)
    elif args.group == "dns" and args.action in {"add", "set", "remove"}:
        if getattr(args, "mxpref", None) is not None and args.type != "MX":
            raise CliError("invalid_record", "mxpref is only valid on MX records.")
        if args.action == "remove" and args.value is None:
            relative_name(args.host, domain)
        else:
            make_host(
                domain,
                args.host,
                args.type,
                args.value,
                getattr(args, "ttl", 1800),
                DEFAULT_MXPREF if getattr(args, "mxpref", None) is None else args.mxpref,
            )

    credentials = Credentials.load(path, profile, env)
    secrets.append(credentials.api_key)
    address = (
        ipv4(env["NAMECHEAP_CLIENT_IP"]) if env.get("NAMECHEAP_CLIENT_IP") else detect_ipv4(profile)
    )
    api = client_factory(credentials, address, profile)
    service = Service(config, api, state_path(env))
    if args.group == "doctor":
        rows = api.domains()
        names = {row["name"] for row in rows}
        absent = sorted(set(config.allowed_domains) - names)
        return {
            "profile": profile,
            "client_ip": address,
            "api": "ok",
            "config": str(path),
            "allowed_domains": list(config.allowed_domains),
            "missing_domains": absent,
            "warnings": ["Some allowed domains are not in this account."] if absent else [],
        }
    if args.group == "domains":
        if args.action == "check":
            return {"results": api.check(checked_names)}
        all_rows = api.domains()
        rows = [row for row in all_rows if args.all or row["name"] in config.allowed_domains]
        return {"domains": rows, "hidden_count": len(all_rows) - len(rows)}
    if args.group == "restore":
        return service.restore(args.file, **write)
    if args.group == "ns":
        return (
            service.ns_list(domain)
            if args.action == "list"
            else service.ns_set(domain, servers=args.custom, default=args.default, **write)
        )
    if args.action == "list":
        return service.dns_list(domain)
    if args.action == "check":
        return service.dns_check(domain, host=args.host, rtype=args.rtype, resolver=doh_resolver)
    if args.action == "apply":
        return service.dns_edit(
            domain, "apply", zone_data=zone_data, email_type=args.email_type, **write
        )
    mxpref = DEFAULT_MXPREF if getattr(args, "mxpref", None) is None else args.mxpref
    return service.dns_edit(
        domain,
        args.action,
        name=args.host,
        rtype=args.type,
        value=args.value,
        ttl=getattr(args, "ttl", 1800),
        mxpref=mxpref,
        email_type=args.email_type,
        **write,
    )


def redact(value: Any, secrets: list[str]) -> Any:
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, list):
        return [redact(item, secrets) for item in value]
    if isinstance(value, dict):
        return {key: redact(item, secrets) for key, item in value.items()}
    return value


def render_human(envelope: dict) -> str:
    if not envelope["ok"]:
        error = envelope["error"]
        return f"{error['code']}: {error['message']}\n" + json.dumps(error["details"], indent=2)
    data = envelope["data"]
    if "dry_run" in data:
        status = (
            "no change" if not data["changed"] else "dry run" if data["dry_run"] else "verified"
        )
        result = [f"{data['domain']}: {status}", f"state hash: {data['state_hash']}"]
        for item in data["diff"]:
            result.append(json.dumps(item, ensure_ascii=False))
        if data.get("recovery"):
            result.append("backup: " + data["recovery"]["backup_file"])
        result.extend("warning: " + warning for warning in envelope["warnings"])
        return "\n".join(result)
    return json.dumps(data, indent=2, ensure_ascii=False)


def main(
    argv: list[str] | None = None,
    *,
    env=None,
    client_factory=Client,
    stdin: Any = None,
    doh_resolver=resolve_doh,
) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if env is None else env
    secrets = [
        value
        for key, value in env.items()
        if key in {"NAMECHEAP_API_KEY", "NAMECHEAP_SANDBOX_API_KEY"}
    ]
    human = "--human" in argv and "--json" not in argv
    command = ""
    try:
        args = build_parser().parse_args(argv)
        human = getattr(args, "human", False) and not getattr(args, "json", False)
        command = args.group + (" " + args.action if hasattr(args, "action") else "")
        data = dispatch(args, env, secrets, client_factory, stdin=stdin, doh_resolver=doh_resolver)
        warnings = data.pop("warnings", [])
        result = {
            "schema_version": 1,
            "ok": True,
            "command": command,
            "data": data,
            "warnings": warnings,
        }
        exit_code = 0
    except CliError as exc:
        result = {"schema_version": 1, "ok": False, "command": command, "error": exc.to_json()}
        exit_code = exc.exit_code
    except OSError as exc:
        error = CliError(
            "local_io_error",
            "Could not read or save a local file.",
            details={"exception": type(exc).__name__},
        )
        result = {"schema_version": 1, "ok": False, "command": command, "error": error.to_json()}
        exit_code = 2
    except KeyboardInterrupt:
        result = {
            "schema_version": 1,
            "ok": False,
            "command": command,
            "error": CliError("interrupted", "Interrupted before submission.").to_json(),
        }
        exit_code = 130
    except SystemExit as exc:
        return int(exc.code or 0)
    result = redact(result, secrets)
    output = render_human(result) if human else json.dumps(result, indent=2, ensure_ascii=False)
    try:
        print(output)
    except BrokenPipeError:
        return exit_code
    return exit_code
