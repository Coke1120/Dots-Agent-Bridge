"""Bounded async long-polling, no detached work and no lock held during sleep."""
import asyncio
import json
import time

from starlette.concurrency import run_in_threadpool

from .auth import Denied
from .store import canonical


class Waiter:
    def __init__(self, limit=4, interval=1, clock=time.monotonic, sleep=asyncio.sleep):
        self.active = 0
        self.limit, self.interval, self.clock, self.sleep = limit, interval, clock, sleep

    async def run(self, request, auth, principal, protocol, params):
        # Single ASGI event loop: no await between capacity check and increment.
        if self.active >= self.limit:
            return {'isError': True, 'content': [{'type': 'text', 'text': 'wait_capacity_retry_later'}]}
        self.active += 1
        try:
            deadline = None
            while True:
                # Expired/revoked credentials cannot continue a previously accepted wait.
                current = await run_in_threadpool(auth, request.headers.get('authorization'))
                if current != principal:
                    raise Denied()
                result = await run_in_threadpool(protocol.dispatch, current, 'tools/call', params)
                if result.get('isError'):
                    return result
                snapshot = json.loads(result['content'][0]['text'])
                if deadline is None:
                    # params have now passed the protocol's bounded argument schema.
                    deadline = self.clock() + params['arguments'].get('timeout_seconds', 25)
                remaining = deadline - self.clock()
                if snapshot['ready'] or remaining <= 0:
                    snapshot['timed_out'] = not snapshot['ready']
                    result['content'][0]['text'] = canonical(snapshot)
                    return result
                if await request.is_disconnected():
                    raise asyncio.CancelledError()
                await self.sleep(min(self.interval, remaining))
        finally:
            self.active -= 1
