"""MCP 2026 per-request transport checks; metadata never grants identity/roles."""
import base64
import binascii

LEGACY = '2025-11-25'
MODERN = '2026-07-28'
SUPPORTED = [MODERN, LEGACY]
PREFIX = 'io.modelcontextprotocol/'
SERVER_INFO = {'name': 'hermes-dots-bridge', 'version': '0.1.0'}


class WireError(Exception):
    def __init__(self, code, message, data=None):
        self.code, self.message, self.data = code, message, data


def request_version(headers, method, params):
    meta = params.get('_meta', {})
    header = headers.get('mcp-protocol-version')
    body_version = meta.get(PREFIX + 'protocolVersion') if isinstance(meta, dict) else None
    modern = method == 'server/discover' or body_version is not None or header not in (None, LEGACY)
    if not modern:
        return LEGACY
    if not isinstance(meta, dict) or not isinstance(body_version, str) or not isinstance(meta.get(PREFIX + 'clientCapabilities'), dict):
        raise WireError(-32602, 'Required per-request metadata missing or malformed')
    info = meta.get(PREFIX + 'clientInfo')
    if info is not None and (not isinstance(info, dict) or not all(isinstance(info.get(k), str) for k in ('name', 'version'))):
        raise WireError(-32602, 'Malformed clientInfo')
    expected = {'mcp-protocol-version': body_version, 'mcp-method': method}
    if method in ('tools/call', 'prompts/get', 'resources/read'):
        expected['mcp-name'] = params.get('uri' if method == 'resources/read' else 'name')
    for name, value in expected.items():
        raw = headers.get(name)
        if len(headers.getlist(name)) != 1 or not isinstance(value, str) or raw is None:
            raise WireError(-32020, 'Required MCP header missing or malformed')
        if any(ord(c) < 32 or ord(c) > 126 for c in raw):
            raise WireError(-32020, 'Malformed MCP header')
        if name == 'mcp-name' and raw.startswith('=?base64?') and raw.endswith('?='):
            try:
                raw = base64.b64decode(raw[9:-2], validate=True).decode('utf-8')
            except (ValueError, UnicodeError, binascii.Error):
                raise WireError(-32020, 'Malformed encoded MCP header') from None
        if raw != value:
            raise WireError(-32020, 'MCP header does not match request body')
    if body_version not in SUPPORTED:
        raise WireError(-32022, 'Unsupported protocol version', {'supported': SUPPORTED, 'requested': body_version})
    if body_version != MODERN:
        raise WireError(-32602, 'Per-request metadata requires a modern protocol version')
    return MODERN


def complete(result, method):
    result = {**result, 'resultType': 'complete', '_meta': {
        **result.get('_meta', {}), PREFIX + 'serverInfo': SERVER_INFO}}
    if method in ('server/discover', 'tools/list', 'events/list'):
        # Results vary with verified grants, which can be revoked on every call.
        result['ttlMs'] = 0
        result['cacheScope'] = 'private'
    return result
