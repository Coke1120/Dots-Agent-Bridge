"""Deterministic client using an installed Hermes MCP transport, never its agent loop.

Run with Hermes's Python: python /path/to/repo/bridge/direct.py --help.
Hermes private transport APIs are version-sensitive; fail closed on incompatibility.
"""
import argparse
import asyncio
import hashlib
import importlib.abc
import json
import os
from pathlib import Path
import sys
import uuid


class ClientError(Exception):
    pass


class NoInference(importlib.abc.MetaPathFinder):
    # MCP uses agent.async_utils/secret_scope; those are infrastructure, not inference.
    blocked = ('run_agent', 'agent.run', 'agent.model', 'agent.providers',
               'hermes_cli.main', 'litellm', 'openai', 'anthropic')

    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == p or fullname.startswith(p + '.') for p in self.blocked):
            raise ClientError('Inference module import refused')
        return None


def decode(result):
    value = result.model_dump(mode='json', by_alias=True)
    if value.get('isError', value.get('is_error', False)):
        raise ClientError('MCP tool returned an error; inspect authentication/state safely')
    try:
        texts = [c['text'] for c in value['content'] if c.get('type') == 'text']
        if len(texts) != 1:
            raise ValueError()
        data = json.loads(texts[0])
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except (KeyError, TypeError, ValueError):
        raise ClientError('Invalid tool result; submission may have succeeded') from None


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def prepare_receipt(path, home, server, arguments):
    """Persist retry identity before network work; store no message text or tokens."""
    if path.parent.stat().st_mode & 0o077:
        raise ClientError('Receipt directory must be private (mode 700)')
    fingerprint = hashlib.sha256(json.dumps([str(home), server, arguments], sort_keys=True).encode()).hexdigest()
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file() or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
            raise ClientError('Receipt must be an owned private regular file')
        receipt = json.loads(path.read_text())
        if receipt.get('fingerprint') != fingerprint:
            raise ClientError('Receipt belongs to different input/profile/server; do not reuse')
    else:
        receipt = {'fingerprint': fingerprint, 'idempotency_key': str(uuid.uuid4())}
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as f:
            json.dump(receipt, f)
            f.flush()
            os.fsync(f.fileno())
    if not isinstance(receipt.get('idempotency_key'), str) or not receipt['idempotency_key']:
        raise ClientError('Invalid receipt')
    return receipt


async def exchange(connect, config, args, receipt=None):
    server = await asyncio.wait_for(connect(args.server, config), timeout=60)
    try:
        async def call(name, arguments):
            return decode(await asyncio.wait_for(server.session.call_tool(name, arguments), timeout=45))
        if args.command == 'send':
            submitted = await call('submit_message', {**args.arguments, 'idempotency_key': receipt['idempotency_key']})
            request_id = submitted['id']
            emit({'request_id': request_id, 'idempotency_key': receipt['idempotency_key']})
        else:
            request_id = args.request_id
        if args.command == 'status':
            emit(await call('get_request', {'request_id': request_id}))
            after = 0
            for _ in range(20):
                page = await call('list_responses', {'request_id': request_id, 'after_revision': after})
                emit(page)
                if not page.get('next_after_revision'):
                    return
                after = page['next_after_revision']
            raise ClientError('Response page limit reached')
        else:
            after = 0
            for _ in range(args.wait_calls):
                snapshot = await call('wait_response', {'request_id': request_id, 'after_revision': after, 'timeout_seconds': 25})
                emit(snapshot)
                after = snapshot.get('next_after_revision') or max([after] + [r['revision'] for r in snapshot['responses']])
                if snapshot.get('next_after_revision'):
                    continue
                if snapshot['state'] in {'completed', 'failed', 'cancelled', 'expired', 'needs_input'}:
                    return
                if not snapshot['active_subscriptions']:
                    break
            emit({'request_id': request_id, 'waiting_stopped': True, 'action': 'Use status or wait with this ID; do not submit a new request'})
    finally:
        await server.shutdown()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hermes-home', type=Path, required=True)
    parser.add_argument('--server', default='hermes-dots-bridge')
    sub = parser.add_subparsers(dest='command', required=True)
    send = sub.add_parser('send')
    send.add_argument('message', help='Use - to read stdin instead of shell history')
    send.add_argument('--conversation-id')
    send.add_argument('--receipt', type=Path, required=True, help='Private retry receipt; reuse with identical message on uncertain submission')
    send.add_argument('--wait-calls', type=int, choices=range(1, 7), default=6)
    sub.add_parser('status').add_argument('request_id')
    wait = sub.add_parser('wait')
    wait.add_argument('request_id')
    wait.add_argument('--wait-calls', type=int, choices=range(1, 7), default=6)
    args = parser.parse_args(argv)
    guard = NoInference()
    try:
        home = args.hermes_home.expanduser().resolve(strict=True)
        if not home.is_dir() or not (home / 'config.yaml').is_file():
            raise ClientError('Explicit existing Hermes profile with config.yaml required')
        # Standalone process only; never switch an already initialized Hermes process.
        if any(n == 'hermes_constants' or n.startswith(('hermes_cli.', 'tools.mcp_')) for n in sys.modules):
            raise ClientError('Run in a fresh process before Hermes configuration imports')
        os.environ['HERMES_HOME'] = str(home)
        sys.meta_path.insert(0, guard)
        receipt = None
        if args.command == 'send':
            message = sys.stdin.read(8001) if args.message == '-' else args.message
            if not message or len(message) > 8000:
                raise ClientError('Message must contain 1..8000 characters')
            args.arguments = {'content': message}
            if args.conversation_id:
                args.arguments['conversation_id'] = args.conversation_id
            receipt = prepare_receipt(args.receipt, home, args.server, args.arguments)
            emit({'idempotency_key': receipt['idempotency_key'], 'receipt_saved': True})
        from hermes_cli.mcp_config import _get_mcp_servers, _resolve_mcp_server_config
        from tools.mcp_tool_discovery import _connect_server
        from tools.mcp_tool_loop import _ensure_mcp_loop, _run_on_mcp_loop
        from tools.mcp_tool_lifecycle import _stop_mcp_loop_if_idle
        config = _resolve_mcp_server_config(_get_mcp_servers()[args.server])
        if config.get('auth') != 'oauth' or config.get('command') or not str(config.get('url', '')).startswith('https://'):
            raise ClientError('Only configured HTTPS OAuth MCP transport is supported')
        _ensure_mcp_loop()
        try:
            _run_on_mcp_loop(lambda: exchange(_connect_server, config, args, receipt), timeout=400)
        finally:
            _stop_mcp_loop_if_idle()
        return 0
    except (Exception, KeyboardInterrupt):
        # Do not print third-party exceptions: they can contain tokens or callback URLs.
        print('Direct MCP operation failed or interrupted. Reuse the saved receipt with identical input after an uncertain send; use status/wait if the request ID is known. Check profile login and transport compatibility.', file=sys.stderr)
        return 1
    finally:
        if guard in sys.meta_path:
            sys.meta_path.remove(guard)


if __name__ == '__main__':
    raise SystemExit(main())
