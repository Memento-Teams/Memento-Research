"""Unit tests for api/websocket.py — WebSocket connection manager."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# WebSocketManager
# ---------------------------------------------------------------------------


class TestWebSocketManagerConnect:
    async def test_connect_accepts_and_adds(self):
        from onemancompany.api.websocket import WebSocketManager

        mgr = WebSocketManager()
        ws = AsyncMock()

        await mgr.connect(ws)

        ws.accept.assert_called_once()
        assert ws in mgr.connections
        ws.send_json.assert_called_once()
        sent_data = ws.send_json.call_args[0][0]
        assert sent_data["type"] == "connected"
        assert sent_data["payload"]["message"] == "Bootstrap from REST API"

    async def test_connect_multiple(self):
        from onemancompany.api.websocket import WebSocketManager

        mgr = WebSocketManager()
        ws1 = AsyncMock()
        ws2 = AsyncMock()

        await mgr.connect(ws1)
        await mgr.connect(ws2)

        assert len(mgr.connections) == 2


class TestWebSocketManagerDisconnect:
    def test_disconnect_removes(self):
        from onemancompany.api.websocket import WebSocketManager

        mgr = WebSocketManager()
        ws = MagicMock()
        mgr.connections[ws] = ""

        mgr.disconnect(ws)
        assert ws not in mgr.connections

    def test_disconnect_nonexistent_no_error(self):
        from onemancompany.api.websocket import WebSocketManager

        mgr = WebSocketManager()
        ws = MagicMock()
        mgr.disconnect(ws)  # Should not raise
        assert len(mgr.connections) == 0


class TestWebSocketManagerBroadcast:
    async def test_broadcast_sends_to_all(self):
        from onemancompany.api.websocket import WebSocketManager

        mgr = WebSocketManager()
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        mgr.connections = {ws1: "", ws2: ""}

        message = {"type": "test", "data": "hello"}
        await mgr.broadcast(message)

        ws1.send_json.assert_called_once_with(message)
        ws2.send_json.assert_called_once_with(message)

    async def test_broadcast_removes_dead_connections(self):
        from onemancompany.api.websocket import WebSocketManager

        mgr = WebSocketManager()
        ws_alive = AsyncMock()
        ws_dead = AsyncMock()
        ws_dead.send_json.side_effect = Exception("Connection closed")
        mgr.connections = {ws_alive: "", ws_dead: ""}

        await mgr.broadcast({"type": "test"})

        assert ws_alive in mgr.connections
        assert ws_dead not in mgr.connections
        assert len(mgr.connections) == 1

    async def test_broadcast_empty_connections(self):
        from onemancompany.api.websocket import WebSocketManager

        mgr = WebSocketManager()
        await mgr.broadcast({"type": "test"})  # Should not raise

    async def test_broadcast_all_dead(self):
        from onemancompany.api.websocket import WebSocketManager

        mgr = WebSocketManager()
        ws1 = AsyncMock()
        ws1.send_json.side_effect = Exception("dead")
        ws2 = AsyncMock()
        ws2.send_json.side_effect = Exception("dead")
        mgr.connections = {ws1: "", ws2: ""}

        await mgr.broadcast({"type": "test"})
        assert len(mgr.connections) == 0


class TestEventBroadcaster:
    async def test_broadcasts_events_from_bus(self):
        from onemancompany.core.events import CompanyEvent, EventBus
        from onemancompany.api.websocket import WebSocketManager

        mgr = WebSocketManager()
        ws = AsyncMock()
        mgr.connections = {ws: ""}

        bus = EventBus()

        with patch("onemancompany.api.websocket.event_bus", bus):
            # Start the broadcaster in a task
            task = asyncio.create_task(mgr.event_broadcaster())

            # Give it time to subscribe
            await asyncio.sleep(0.01)

            # Publish an event
            await bus.publish(CompanyEvent(type="agent_done", payload={"role": "HR"}, agent="HR"))

            # Give it time to process
            await asyncio.sleep(0.01)

            # Cancel the broadcaster
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            # Verify the websocket received the broadcast
            ws.send_json.assert_called()
            sent = ws.send_json.call_args[0][0]
            assert sent["type"] == "agent_done"
            assert sent["agent"] == "HR"
            # No full state attached
            assert "state" not in sent

    async def test_unsubscribes_on_cancel(self):
        from onemancompany.core.events import EventBus
        from onemancompany.api.websocket import WebSocketManager

        mgr = WebSocketManager()
        bus = EventBus()

        with patch("onemancompany.api.websocket.event_bus", bus):
            task = asyncio.create_task(mgr.event_broadcaster())
            await asyncio.sleep(0.01)
            assert len(bus._subscribers) == 1

            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            assert len(bus._subscribers) == 0


# ---------------------------------------------------------------------------
# Per-user isolation (issue #115) — a logged-in user must NOT receive another
# user's project events in real time.
# ---------------------------------------------------------------------------


class TestWebSocketUserIsolation:
    async def test_project_event_goes_only_to_owner(self):
        """A broadcast owned by alice reaches alice's connection, NOT bob's."""
        from onemancompany.api.websocket import WebSocketManager

        mgr = WebSocketManager()
        alice = AsyncMock()
        bob = AsyncMock()
        mgr.connections = {alice: "alice", bob: "bob"}

        await mgr.broadcast({"type": "stage_start", "payload": {"project_id": "p1"}}, owner="alice")

        alice.send_json.assert_called_once()
        bob.send_json.assert_not_called()

    async def test_unauthenticated_connection_sees_everything(self):
        """A connection with user_id "" (AUTH off / no cookie) receives a
        project-owned event too — zero regression for single-user/local."""
        from onemancompany.api.websocket import WebSocketManager

        mgr = WebSocketManager()
        anon = AsyncMock()
        mgr.connections = {anon: ""}

        await mgr.broadcast({"type": "stage_start", "payload": {"project_id": "p1"}}, owner="alice")

        anon.send_json.assert_called_once()

    async def test_system_event_no_owner_broadcasts_to_all(self):
        """owner="" (unattributable / system event) reaches every connection."""
        from onemancompany.api.websocket import WebSocketManager

        mgr = WebSocketManager()
        alice = AsyncMock()
        bob = AsyncMock()
        mgr.connections = {alice: "alice", bob: "bob"}

        await mgr.broadcast({"type": "open_popup", "payload": {"message": "hi"}}, owner="")

        alice.send_json.assert_called_once()
        bob.send_json.assert_called_once()

    async def test_broadcaster_routes_by_project_owner(self):
        """End-to-end: an event carrying project_id is routed to that project's
        owner's connection only, resolved via get_project_owner."""
        from onemancompany.core.events import CompanyEvent, EventBus
        from onemancompany.api.websocket import WebSocketManager

        mgr = WebSocketManager()
        alice = AsyncMock()
        bob = AsyncMock()
        mgr.connections = {alice: "alice", bob: "bob"}

        bus = EventBus()
        with patch("onemancompany.api.websocket.event_bus", bus), \
             patch("onemancompany.core.user_llm.get_project_owner", return_value="alice"):
            task = asyncio.create_task(mgr.event_broadcaster())
            await asyncio.sleep(0.01)
            await bus.publish(CompanyEvent(
                type="stage_start", payload={"project_id": "p1", "stage": 6}, agent="00016"))
            await asyncio.sleep(0.01)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        alice.send_json.assert_called()
        bob.send_json.assert_not_called()  # bob must not see alice's pipeline

    async def test_unknown_owner_fails_open(self):
        """Event has a project_id but owner lookup returns "" → fail-open
        (broadcast to all) rather than silently dropping a real event."""
        from onemancompany.core.events import CompanyEvent, EventBus
        from onemancompany.api.websocket import WebSocketManager

        mgr = WebSocketManager()
        alice = AsyncMock()
        bob = AsyncMock()
        mgr.connections = {alice: "alice", bob: "bob"}

        bus = EventBus()
        with patch("onemancompany.api.websocket.event_bus", bus), \
             patch("onemancompany.core.user_llm.get_project_owner", return_value=""):
            task = asyncio.create_task(mgr.event_broadcaster())
            await asyncio.sleep(0.01)
            await bus.publish(CompanyEvent(
                type="stage_start", payload={"project_id": "p_unknown"}, agent="x"))
            await asyncio.sleep(0.01)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        alice.send_json.assert_called()
        bob.send_json.assert_called()

    async def test_connect_binds_user_id(self):
        """connect(ws, user_id=...) records the owner for routing."""
        from onemancompany.api.websocket import WebSocketManager

        mgr = WebSocketManager()
        ws = AsyncMock()
        await mgr.connect(ws, user_id="carol")
        assert mgr.connections[ws] == "carol"
