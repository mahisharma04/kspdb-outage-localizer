"""Tiny in-process pub/sub for Server-Sent Events.

We chose SSE over WebSockets deliberately: the console only needs server->client
push, SSE is plain HTTP so it survives free-tier reverse proxies that mishandle
WebSocket upgrades (a classic deploy failure called out in the brief), and it
auto-reconnects in the browser. See DECISIONS.md.
"""
from __future__ import annotations

import asyncio


class EventBus:
    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    async def publish(self, event: dict) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass
