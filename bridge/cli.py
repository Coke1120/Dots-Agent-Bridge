"""User-controlled local service packaging. No installer, login or tunnel side effects."""
import argparse
import json
import os
import plistlib
import stat
import ssl
import sqlite3
import time
import sys
from pathlib import Path

from cryptography.fernet import Fernet

from .auth import Denied, DISPATCH_SCOPE, JWTAuth, Policy, SCOPES, validate_https, validate_local_resource
from .callback import Callback

LABEL = "com.hermes-dots-bridge.service"
FIELDS = {"resource", "issuer", "jwks_url", "client_claim", "tunnel_resource",
          "policy_file", "database", "encryption_key_file", "callback_hosts"}


class SetupError(Exception):
    pass


def private_file(path):
    """No secrets/config from symlinks, other owners or group/world-readable files."""
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise SetupError("Configuration, policy and key must be owned regular files with mode 600")
    if info.st_size > 65536:
        raise SetupError("Configuration file exceeds size limit")
    return path.read_text()


def init_directory(directory, submitter="hermes"):
    if submitter not in ("hermes", "agent"):
        raise SetupError("Submitter must be hermes or agent")
    directory = directory.expanduser().absolute()
    if directory.exists():
        raise SetupError("Destination already exists; choose a new directory (nothing overwritten)")
    directory.mkdir(parents=True, mode=0o700)
    config = {
        "resource": "http://127.0.0.1:8765/mcp",
        "issuer": "https://REPLACE_WITH_PROVIDER/",
        "jwks_url": "https://REPLACE_WITH_PROVIDER/.well-known/jwks.json",
        "client_claim": "azp",
        "tunnel_resource": None,
        "policy_file": "policy.json", "database": "bridge.sqlite",
        "encryption_key_file": "signing-secrets.key",
        "callback_hosts": ["REPLACE_WITH_VERIFIED_CHATGPT_CALLBACK_HOST"],
    }
    policy = {"grants": [{"subject": "REPLACE_WITH_VERIFIED_SUBJECT", "client_id": "REPLACE_WITH_" + role.upper() + "_CLIENT_ID",
                          "owner": "private-owner", "role": role, "enabled": False, "queue_authorized": False}
                         for role in (submitter, "dot")]}
    for name, value in (("config.json", config), ("policy.json", policy)):
        with (directory / name).open("x") as output:
            os.chmod(output.name, 0o600)
            output.write(json.dumps(value, indent=2) + "\n")
    return directory / "config.json"


def load_config(path, ready=False):
    path = path.expanduser().absolute()
    if path.is_symlink():
        raise SetupError("Configuration symlinks are not supported")
    config = json.loads(private_file(path))
    if not isinstance(config, dict) or not FIELDS <= set(config) or set(config) - FIELDS - {"tls_cert_file", "tls_key_file"}:
        raise SetupError("Configuration fields must match the generated template")
    validate_local_resource(config["resource"])
    validate_https(config["issuer"])
    validate_https(config["jwks_url"])
    if config["client_claim"] not in ("azp", "client_id"):
        raise SetupError("client_claim must be azp or client_id")
    if config["tunnel_resource"] is not None:
        validate_https(config["tunnel_resource"])
    for field in ("policy_file", "database", "encryption_key_file"):
        value = Path(config[field]).expanduser()
        config[field] = str(value if value.is_absolute() else path.parent / value)
    if not isinstance(config["callback_hosts"], list) or not config["callback_hosts"] or not all(isinstance(h, str) and h and "/" not in h and ":" not in h for h in config["callback_hosts"]):
        raise SetupError("callback_hosts must contain exact hostnames")
    tls_options(config, path.parent)
    if ready:
        if "REPLACE_" in json.dumps(config):
            raise SetupError("Replace configuration placeholders before starting")
        db = Path(config["database"])
        parent = db.parent.stat()
        if parent.st_uid != os.getuid() or parent.st_mode & 0o077 or db.is_symlink():
            raise SetupError("Database needs an owned private directory (mode 700), without a database symlink")
        if db.exists():
            check_database_permissions(db)
        key = private_file(Path(config["encryption_key_file"])).strip()
        Fernet(key.encode())
        policy = json.loads(private_file(Path(config["policy_file"])))
        if not isinstance(policy, dict):
            raise SetupError("Policy must contain a grants array")
        grants = policy.get("grants", [])
        if not isinstance(grants, list) or not all(isinstance(g, dict) for g in grants):
            raise SetupError("Policy must contain a grants array")
        if "REPLACE_" in json.dumps(policy) or not grants:
            raise SetupError("Populate verified operator grants before starting")
        resolver = Policy(config["policy_file"])
        active = [g for g in grants if g.get("enabled") is True and g.get("queue_authorized") is True]
        if any((g.get("enabled") is True) != (g.get("queue_authorized") is True) for g in grants):
            raise SetupError("Grants must be enabled and queue-authorized together")
        if len([g for g in active if g.get("role") == "dot"]) != 1:
            raise SetupError("Local bridge needs exactly one authorized dot grant")
        has_submitter = any(g.get("role") in ("hermes", "agent") for g in active)
        dispatch_enabled = bool(os.environ.get("BRIDGE_OPENBOT_AUTOMATION_DIR"))
        for grant in active:
            if grant.get("role") not in SCOPES:
                raise SetupError("Unrecognized active bridge role")
            if not all(isinstance(grant.get(k), str) and grant[k] for k in ("subject", "client_id", "owner")):
                raise SetupError("Grant identities must be nonempty strings")
            try:
                principal = resolver.resolve(grant["subject"], grant["client_id"], [DISPATCH_SCOPE])
            except Denied:
                raise SetupError("Ambiguous or unauthorized grant identity") from None
            if dispatch_enabled and DISPATCH_SCOPE in principal.scopes:
                has_submitter = True
        if not has_submitter:
            raise SetupError("Local bridge needs an authorized mailbox submitter or dispatch-enabled dot")
        if len({g["owner"] for g in active}) != 1:
            raise SetupError("All active bridge grants must map to one owner")
    return config


def tls_options(config, directory=None):
    """Explicit process-local TLS; never install a CA or generate credentials."""
    secure = config["resource"].startswith("https:")
    fields = ("tls_cert_file", "tls_key_file")
    present = [bool(config.get(k)) for k in fields]
    if secure != all(present) or any(present) != all(present):
        raise SetupError("HTTPS needs both tls_cert_file and tls_key_file; HTTP must not specify TLS files")
    if not secure:
        return {}
    for key in fields:
        path = Path(config[key]).expanduser()
        if not path.is_absolute():
            if directory is None:
                raise SetupError("TLS paths must be resolved before serving")
            path = directory / path
        private_file(path)
        config[key] = str(path)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(config['tls_cert_file'], config['tls_key_file'])
    return {'ssl_certfile': config['tls_cert_file'], 'ssl_keyfile': config['tls_key_file']}


def local_status(config):
    """Read-only aggregate status; no contents, credentials or callback URLs."""
    import fcntl
    db = Path(config['database'])
    result = {'database_exists': db.exists(), 'process_lock_held': False,
              'https_configured': config['resource'].startswith('https:'),
              'tunnel_audience_configured': bool(config['tunnel_resource'])}
    lock = db.with_suffix('.lock')
    if lock.exists():
        private_file(lock)
        with lock.open('r') as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(stream, fcntl.LOCK_UN)
            except BlockingIOError:
                result['process_lock_held'] = True
    if db.exists():
        check_database_permissions(db)
        with sqlite3.connect(db.absolute().as_uri() + '?mode=ro', uri=True) as connection:
            rows = [(kind, json.loads(body)) for kind, body in connection.execute(
                "SELECT kind,body FROM records WHERE kind IN ('subscription','outbox','request')")]
        now = time.time()
        subs = [r for kind, r in rows if kind == 'subscription' and r['active'] and r['expires'] > now]
        result.update(unexpired_subscription_records=len(subs),
                      next_subscription_expiry=min((r['expires'] for r in subs), default=None),
                      pending_deliveries=sum(k == 'outbox' and r['status'] == 'pending' for k, r in rows),
                      failed_deliveries=sum(k == 'outbox' and r['status'] == 'failed' for k, r in rows))
    result['note'] = 'Local snapshot only; not proof of token validity, tunnel health, subscription authorization or dot processing.'
    return result


def check_database_permissions(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise SetupError("Database must be an owned regular file with mode 600")


def hermes_snippet(config):
    return ('mcp_servers:\n  hermes-dots-bridge:\n    url: ' + json.dumps(config["resource"]) + '\n'
            '    auth: oauth\n    oauth:\n      client_id: "${BRIDGE_HERMES_CLIENT_ID}"\n'
            '      scope: "bridge:submit bridge:read offline_access"\n      redirect_host: localhost\n'
            '      redirect_port: 27890\n    timeout: 60\n')


def launchd_template(config_path, keep_alive=False):
    config_path = config_path.expanduser().absolute()
    # plistlib escapes spaces and XML characters; never a shell command/string.
    return plistlib.dumps({"Label": LABEL,
                          "ProgramArguments": [sys.executable, "-m", "bridge.cli", "serve", "--config", str(config_path)],
                          "WorkingDirectory": str(config_path.parent),
                          "Disabled": True, "RunAtLoad": True, "KeepAlive": keep_alive,
                          "ThrottleInterval": 30,
                          "ProcessType": "Background", "Umask": 0o077,
                          "StandardOutPath": str(config_path.parent / "service.log"),
                          "StandardErrorPath": str(config_path.parent / "service.log")})


def tunnel_launchd_template(binary, profile_file, ca_file):
    paths = [p.expanduser().absolute() for p in (binary, profile_file, ca_file)]
    return plistlib.dumps({"Label": LABEL + ".tunnel", "Disabled": True,
        "ProgramArguments": [str(paths[0]), "run", "--config", str(paths[1]), "--ca-bundle", str(paths[2])],
        "WorkingDirectory": str(paths[1].parent), "RunAtLoad": True, "KeepAlive": True,
        "ThrottleInterval": 30, "ProcessType": "Background", "Umask": 0o077,
        "StandardOutPath": str(paths[1].parent / "tunnel.log"),
        "StandardErrorPath": str(paths[1].parent / "tunnel.log")})


def serve(config):
    import fcntl
    import uvicorn
    from .app import create_app
    from .store import Store
    os.umask(0o077)
    db = Path(config["database"])
    lock_path = db.with_suffix(".lock")
    # Prevent a second foreground/launchd instance; lock file never contains secrets.
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SetupError("Another bridge process holds this database lock") from None
        policy = Policy(config["policy_file"])
        auth = JWTAuth(config["issuer"], config["resource"], config["jwks_url"], policy,
                       config["client_claim"], allow_loopback=True, tunnel_resource=config["tunnel_resource"])
        store = Store(str(db), private_file(Path(config["encryption_key_file"])).strip(), policy, Callback(config["callback_hosts"]))
        app = create_app(store, auth, config["resource"], config["issuer"],
                         openbot_directory=os.environ.get("BRIDGE_OPENBOT_AUTOMATION_DIR"))
        try:
            uvicorn.run(app, host="127.0.0.1", port=validate_local_resource(config["resource"]),
                        workers=1, access_log=False, proxy_headers=False, limit_concurrency=16, **tls_options(config))
        finally:
            store.db.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Private local Hermes-dot MCP bridge")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Write disabled placeholder templates to a new directory only")
    init.add_argument("--directory", type=Path, required=True)
    init.add_argument("--submitter", choices=("hermes", "agent"), default="hermes",
                      help="Create disabled grants for Hermes or an MCP agent such as OpenBot")
    for name in ("check", "status", "serve", "serve-discovery", "print-hermes", "print-launchd"):
        command = sub.add_parser(name)
        command.add_argument("--config", type=Path, required=True)
        if name == "print-launchd":
            command.add_argument("--keep-alive", action="store_true")
        if name == "check":
            command.add_argument("--require-tunnel", action="store_true", help="Fail if the explicit tunnel audience is unset")
    tunnel = sub.add_parser("print-tunnel-launchd", help="Print disabled HTTPS tunnel LaunchAgent; never install")
    tunnel.add_argument("--binary", type=Path, required=True)
    tunnel.add_argument("--profile-file", type=Path, required=True)
    tunnel.add_argument("--ca-file", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "print-tunnel-launchd":
            print(tunnel_launchd_template(args.binary, args.profile_file, args.ca_file).decode(), end="")
            return 0
        if args.command == "init":
            os.umask(0o077)
            print(f"Placeholder templates created: {init_directory(args.directory, args.submitter)}")
            print("No keys, grants, services or Hermes settings were installed.")
            return 0
        config = load_config(args.config, ready=args.command in ("check", "serve"))
        if args.command == "check":
            if args.require_tunnel and not config["tunnel_resource"]:
                raise SetupError("Exact verified tunnel audience is required")
            print("Local configuration checks passed. No network or account validation performed.")
            if not config["tunnel_resource"]:
                print("Tunnel audience is unset: ChatGPT tunnel connection is not ready.")
        elif args.command == "status":
            print(json.dumps(local_status(config), indent=2))
        elif args.command == "print-hermes":
            print(hermes_snippet(config), end="")
        elif args.command == "print-launchd":
            if args.keep_alive and not config["resource"].startswith("https:"):
                raise SetupError("Persistent service template requires local HTTPS configuration")
            print(launchd_template(args.config, args.keep_alive).decode(), end="")
        elif args.command == "serve-discovery":
            if "REPLACE_" in config["issuer"]:
                raise SetupError("Set the verified external issuer before discovery")
            import uvicorn
            from .app import create_discovery_app
            print("Discovery-only: every MCP call is denied; no database or secrets loaded.")
            uvicorn.run(create_discovery_app(config["resource"], config["issuer"],
                        dispatch_enabled=bool(os.environ.get("BRIDGE_OPENBOT_AUTOMATION_DIR"))),
                        host="127.0.0.1", port=validate_local_resource(config["resource"]),
                        workers=1, access_log=False, proxy_headers=False, limit_concurrency=16, **tls_options(config))
        elif args.command == "serve":
            serve(config)
        return 0
    except (SetupError, Denied, OSError, ValueError, TypeError, KeyError) as error:
        # Do not expose config values, token/key contents or JSON parser excerpts.
        print(str(error) if isinstance(error, SetupError) else "Invalid or unreadable local configuration; inspect fields and permissions locally.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
