"""WebSocket connection manager — broadcasts company events to connected clients.

Per-user isolation (issue #115): each connection is bound to the logged-in
user's id (decoded from the auth cookie at connect time). A broadcast that
belongs to a specific project is delivered ONLY to connections owned by that
project's owner — so a logged-in user can no longer watch another user's
pipeline run in real time.

Safe defaults (zero regression for single-user / AUTH-off deployments):
  * Event has no ``project_id``  → system-level (popups, meetings, generic
    state) → broadcast to everyone, as before.
  * Connection's ``user_id`` is "" (AUTH disabled / localhost / no cookie)
    → receives everything, identical to pre-isolation behaviour.
  * Event has a ``project_id`` but its owner is unknown / unrecorded
    → fail-open (broadcast to everyone) rather than drop a real event.
Only an event that is positively attributable to a project with a known
owner is restricted to that owner's connections.
"""

from __future__ import annotations

import asyncio

from fastapi import WebSocket
from loguru import logger

from onemancompany.core.events import CompanyEvent, event_bus


def _event_project_id(payload) -> str:
    """Pull a project id out of an event payload, if present."""
    if not isinstance(payload, dict):
        return ""
    pid = payload.get("project_id") or payload.get("projectId") or ""
    return str(pid)


def _owner_of(project_id: str) -> str:
    """Best-effort project owner lookup; "" when unknown (→ fail-open)."""
    if not project_id:
        return ""
    try:
        from onemancompany.core.user_llm import get_project_owner

        return get_project_owner(project_id) or ""
    except Exception as exc:  # noqa: BLE001
        logger.debug("[ws] owner lookup failed for {}: {}", project_id, exc)
        return ""


class WebSocketManager:
    def __init__(self) -> None:
        # ws → user_id ("" = unauthenticated / AUTH off → sees everything)
        self.connections: dict[WebSocket, str] = {}

    async def connect(self, ws: WebSocket, user_id: str = "") -> None:
        await ws.accept()
        self.connections[ws] = user_id or ""
        # Tell frontend to bootstrap from REST API
        await ws.send_json({
            "type": "connected",
            "payload": {"message": "Bootstrap from REST API"},
        })

    def disconnect(self, ws: WebSocket) -> None:
        self.connections.pop(ws, None)

    _SEND_TIMEOUT = 5  # seconds — drop clients that stall beyond this

    def _recipients(self, owner: str) -> list[WebSocket]:
        """Connections that should receive an event owned by ``owner``.

        owner == "" → unattributable / system event → everyone.
        Otherwise: the owner's own connections, plus any unauthenticated
        connection (user_id == "") so AUTH-off / localhost still sees all.
        """
        if not owner:
            return list(self.connections)
        return [
            ws for ws, uid in self.connections.items()
            if uid == owner or uid == ""
        ]

    async def broadcast(self, message: dict, owner: str = "") -> None:
        """Send ``message`` to the connections entitled to it.

        ``owner`` == "" broadcasts to all (system events / fail-open). When set,
        only that owner's (and unauthenticated) connections receive it.
        """
        targets = self._recipients(owner)
        if not targets:
            return
        dead: set[WebSocket] = set()

        async def _send(ws: WebSocket):
            try:
                await asyncio.wait_for(ws.send_json(message), timeout=self._SEND_TIMEOUT)
            except Exception:
                logger.debug("[ws] Dropping dead/stalled connection")
                dead.add(ws)

        await asyncio.gather(*[_send(ws) for ws in targets])
        for ws in dead:
            self.connections.pop(ws, None)

    async def event_broadcaster(self) -> None:
        """Background task: forward events to WebSocket clients (no full state).

        Project-attributable events are routed to their owner's connections
        only (issue #115); unattributable / owner-unknown events fan out to all.
        """
        queue = event_bus.subscribe()
        try:
            while True:
                event: CompanyEvent = await queue.get()
                owner = _owner_of(_event_project_id(event.payload))
                # Real-time events forwarded directly (chat, popups, etc.)
                # Full state is NOT attached — frontend fetches from REST on tick
                await self.broadcast({
                    "type": event.type,
                    "agent": event.agent,
                    "payload": event.payload,
                }, owner=owner)
        except asyncio.CancelledError:
            raise
        finally:
            event_bus.unsubscribe(queue)


ws_manager = WebSocketManager()
