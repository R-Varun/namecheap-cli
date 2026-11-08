"""Command line contract: one versioned JSON result, including errors."""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .client import Client, detect_ipv4, ipv4
from .config import Config, Credentials, config_path, domain_name, setup_credentials, state_path
from .errors import CliError
from .records import EMAIL_TYPES, TYPES
from .service import Service
from .storage import atomic_json, read_json


class Parser(argparse.ArgumentParser):
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

    config = group("config", "set the domain allowlist")
    p = command(config, "init", "create a configuration with allowed domains")
    p.add_argument("--domain", action="append", default=[], help="allow a domain (repeatable)")
    p = command(config, "allow", "allow one or more domains you are authorized to manage")
    p.add_argument("domains", nargs="+")
    command(config, "show", "show non-secret configuration")
    auth = group("auth", "store credentials from a hidden prompt")
    p = command(
        auth, "setup", "store credentials locally; agents can use environment variables instead"
    )
    p.add_argument("--username", required=True)
    p.add_argument("--force", action="store_true")
    command(groups, "doctor", "check configuration, IPv4, and read-only API access")
    command(groups, "schema", "describe commands, JSON output, and zone-file schemas")
    domains = group("domains", "list domains or check availability")
    p = command(domains, "list", "list allowed domains in the account")
    p.add_argument(
        "--all", action="store_true", help="explicitly include domains outside the allowlist"
    )
    p = command(domains, "check", "check registration availability; never registers a domain")
    p.add_argument("names", nargs="+")
    dns = group("dns", "read and edit BasicDNS records")
    p = command(dns, "list", "return the zone and its state hash")
    p.add_argument("domain")
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
            p.add_argument("--mxpref", type=int, default=10)
        write(p)
        email(p)
    p = command(dns, "apply", "replace the whole zone from a zone JSON file")
    p.add_argument("domain")
    p.add_argument("file", type=Path)
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
                "help": a.help or "",
                "nargs": a.nargs,
                "type": "integer" if a.type is int else "boolean" if a.nargs == 0 else "string",
            }
            if a.choices is not None:
                item["choices"] = list(a.choices)
            args.append(item)
        commands.append(
            {"command": " ".join(path), "description": p.description, "arguments": args}
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


def dispatch(args, env, secrets: list[str], client_factory) -> dict:
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
        else:
            config = Config.load(path)
        return {**config.to_json(), "path": str(path)}
    if args.group == "auth":
        file = setup_credentials(path, profile, args.username, force=args.force)
        return {"stored": str(file), "profile": profile}
    config = Config.load(path)
    # Scope is checked before credentials, IP detection, and network access.
    if hasattr(args, "domain"):
        config.require(args.domain)
    if args.group == "restore":
        saved = read_json(args.file)
        if not isinstance(saved, dict) or not isinstance(saved.get("before"), dict):
            raise CliError("invalid_backup", "Expected a backup object with a before snapshot.")
        config.require(saved["before"].get("domain", ""))
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
            return {"results": api.check(list(dict.fromkeys(domain_name(n) for n in args.names)))}
        all_rows = api.domains()
        rows = [row for row in all_rows if args.all or row["name"] in config.allowed_domains]
        return {"domains": rows, "hidden_count": len(all_rows) - len(rows)}
    write = {"yes": getattr(args, "yes", False), "if_match": getattr(args, "if_match", None)}
    if args.group == "restore":
        return service.restore(args.file, **write)
    if args.group == "ns":
        return (
            service.ns_list(args.domain)
            if args.action == "list"
            else service.ns_set(args.domain, servers=args.custom, default=args.default, **write)
        )
    if args.action == "list":
        return service.dns_list(args.domain)
    if args.action == "apply":
        return service.dns_edit(
            args.domain, "apply", zone_file=args.file, email_type=args.email_type, **write
        )
    return service.dns_edit(
        args.domain,
        args.action,
        name=args.host,
        rtype=args.type,
        value=args.value,
        ttl=getattr(args, "ttl", 1800),
        mxpref=getattr(args, "mxpref", 10),
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


def main(argv: list[str] | None = None, *, env=None, client_factory=Client) -> int:
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
        data = dispatch(args, env, secrets, client_factory)
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
