import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from .auth import Denied, JWTAuth, Policy, SCOPES
from .callback import Callback, CallbackError
from .protocol import Protocol
from .wire import MODERN, WireError, complete, request_version
from .store import Conflict, Store

logger = logging.getLogger(__name__)
MAX_BODY = 65536


def add_metadata_routes(app, resource, issuer):
    @app.get("/.well-known/oauth-protected-resource")
    @app.get("/.well-known/oauth-protected-resource/mcp")
    def metadata():
        return {"resource": resource, "authorization_servers": [issuer],
                "scopes_supported": sorted(set.union(*SCOPES.values())), "bearer_methods_supported": ["header"]}


def create_discovery_app(resource, issuer):
    """Bootstrap only public metadata. No store, credentials, tools or event worker."""
    from .auth import validate_https, validate_local_resource
    validate_local_resource(resource)
    validate_https(issuer)
    origin = resource.removesuffix("/mcp")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    add_metadata_routes(app, resource, issuer)

    @app.api_route("/mcp", methods=["POST", "GET", "DELETE"])
    async def deny_all(request: Request):
        if request.headers.get("origin") not in (None, origin):
            return Response(status_code=403)
        return Response(status_code=401, headers={"WWW-Authenticate":
            f'Bearer resource_metadata="{origin}/.well-known/oauth-protected-resource"'})

    return app


def create_app(store, auth, resource, issuer, run_worker=True):
    protocol = Protocol(store)
    origin = urlsplit(resource).scheme + "://" + urlsplit(resource).netloc
    metadata_url = origin + "/.well-known/oauth-protected-resource"

    @asynccontextmanager
    async def lifespan(app):
        stop = asyncio.Event()
        async def worker():
            while not stop.is_set():
                try:
                    await run_in_threadpool(store.deliver_once)
                except Exception:
                    # Deliberately no exception strings, tokens, URLs, secrets or content.
                    logger.error("delivery_worker_iteration_failed")
                try:
                    await asyncio.wait_for(stop.wait(), timeout=1)
                except TimeoutError:
                    pass
        task = asyncio.create_task(worker()) if run_worker else None
        yield
        stop.set()
        if task:
            await task

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    add_metadata_routes(app, resource, issuer)

    @app.api_route("/mcp", methods=["POST", "GET", "DELETE"])
    async def mcp(request: Request):
        if request.headers.get("origin") not in (None, origin):
            return Response(status_code=403)
        try:
            p = await run_in_threadpool(auth, request.headers.get("authorization"))
        except Denied:
            return Response(status_code=401, headers={"WWW-Authenticate": f'Bearer resource_metadata="{metadata_url}"'})
        if request.method != "POST":
            return Response(status_code=405, headers={"Allow": "POST"})
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            return Response(status_code=415)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_BODY:
                return Response(status_code=413)
        rpc_id = None
        def error(code, message, data=None, status=200):
            item = {"code": code, "message": message}
            if data is not None:
                item["data"] = data
            return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "error": item}, status_code=status, headers={"Cache-Control": "no-store"})
        try:
            msg = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return error(-32700, "Parse error")
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
            return error(-32600, "Invalid request", status=400)
        if "id" not in msg:
            # No mutations accepted as notifications.
            if request.headers.get("mcp-protocol-version") in (None, "2025-11-25") and msg["method"] in ("notifications/initialized", "notifications/cancelled"):
                return Response(status_code=202)
            return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Request id required"}}, status_code=400)
        rpc_id = msg["id"]
        if isinstance(rpc_id, bool) or not isinstance(rpc_id, (str, int)) or len(str(rpc_id)) > 128:
            rpc_id = None
            return error(-32600, "Invalid id")
        params = msg.get("params", {})
        if not isinstance(params, dict):
            return error(-32602, "Invalid params")
        try:
            version = request_version(request.headers, msg["method"], params)
        except WireError as e:
            # Numeric protocol code only: never log bearer tokens, metadata or content.
            logger.warning("mcp_wire_rejected code=%d", e.code)
            return error(e.code, e.message, e.data, status=400)
        try:
            if version == MODERN and msg["method"] == "initialize":
                raise KeyError("legacy_method")
            result = await run_in_threadpool(protocol.dispatch, p, msg["method"], params)
            if version == MODERN:
                result = complete(result, msg["method"])
            return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": result}, headers={"Cache-Control": "no-store"})
        except Denied:
            return error(-32001, "Forbidden")
        except Conflict as e:
            return error(-32602, str(e), status=400 if version == MODERN else 200)
        except CallbackError as e:
            return error(-32015, "CallbackEndpointError", {"reason": e.reason})
        except KeyError:
            return error(-32601, "Method not found", status=404 if version == MODERN else 200)
        except Exception:
            logger.error("mcp_request_failed")
            return error(-32603, "Internal error")

    return app


def production_app():
    # No test/dev auth mode, anonymous option, local token issuer or default key.
    required = ["BRIDGE_RESOURCE", "OAUTH_ISSUER", "OAUTH_JWKS_URL", "BRIDGE_POLICY", "BRIDGE_DB", "BRIDGE_ENCRYPTION_KEY", "BRIDGE_CALLBACK_HOSTS"]
    values = {k: os.environ[k] for k in required}
    os.umask(0o077)
    policy = Policy(values["BRIDGE_POLICY"])
    auth = JWTAuth(values["OAUTH_ISSUER"], values["BRIDGE_RESOURCE"], values["OAUTH_JWKS_URL"], policy, os.environ.get("OAUTH_CLIENT_CLAIM", "client_id"))
    callback = Callback(values["BRIDGE_CALLBACK_HOSTS"].split(","))
    store = Store(values["BRIDGE_DB"], values["BRIDGE_ENCRYPTION_KEY"], policy, callback)
    return create_app(store, auth, values["BRIDGE_RESOURCE"], values["OAUTH_ISSUER"])
