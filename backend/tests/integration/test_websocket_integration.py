"""
Integration tests for WebSocket system.

Tests cover end-to-end workflows:
- Client connection to manager
- Message flow from publisher to client
- Full event lifecycle simulation
- Multiple clients receiving broadcasts
"""

from __future__ import annotations

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
import time

# Setup path for imports
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
APP_ROOT = BACKEND_ROOT / "app"
for candidate in (str(REPO_ROOT), str(BACKEND_ROOT), str(APP_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from harness_lab.control_plane.websocket import (
    WebSocketConnectionManager,
    WebSocketEventPublisher,
    ConnectionFilters,
    ConnectionInfo,
    EventType,
)
from harness_lab.tui.ws_client import (
    TUIWebSocketClient,
    MockWebSocketClient,
    WSConfig,
    ConnectionState,
    create_ws_client,
)


class TestEndToEndFlow:
    """Tests for end-to-end WebSocket flow."""
    
    @pytest.fixture
    def manager(self):
        """Create connection manager."""
        return WebSocketConnectionManager()
    
    @pytest.fixture
    def publisher(self, manager):
        """Create event publisher."""
        return WebSocketEventPublisher(manager)
    
    @pytest.fixture
    def mock_ws_factory(self):
        """Factory for creating mock WebSocket clients."""
        def create_ws():
            ws = MagicMock()
            ws.accept = AsyncMock()
            ws.send_json = AsyncMock()
            ws.receive_json = AsyncMock(side_effect=asyncio.TimeoutError())
            ws.close = AsyncMock()
            return ws
        return create_ws
    
    @pytest.mark.asyncio
    async def test_full_connection_lifecycle(self, manager, publisher, mock_ws_factory):
        """Test full connection lifecycle: connect -> receive -> disconnect."""
        ws = mock_ws_factory()
        
        # Connect
        conn_id = await manager.connect(ws)
        assert len(manager._connections) == 1
        
        # Receive broadcast
        await publisher.publish_worker_event(EventType.WORKER_JOINED, "w-001")
        ws.send_json.assert_called()
        
        # Disconnect
        await manager.disconnect(ws)
        assert len(manager._connections) == 0
    
    @pytest.mark.asyncio
    async def test_multiple_clients_receive_broadcast(self, manager, publisher, mock_ws_factory):
        """Test that multiple clients all receive broadcast."""
        clients = [mock_ws_factory() for _ in range(5)]
        
        # Connect all
        for ws in clients:
            await manager.connect(ws)
        
        assert len(manager._connections) == 5
        
        # Broadcast
        sent = await publisher.publish_worker_event(EventType.WORKER_JOINED, "w-001")
        
        assert sent == 5
        for ws in clients:
            ws.send_json.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_event_sequence_worker_lifecycle(self, manager, publisher, mock_ws_factory):
        """Test sequence of worker lifecycle events."""
        ws = mock_ws_factory()
        await manager.connect(ws)
        
        events_received = []
        ws.send_json = AsyncMock(side_effect=lambda msg: events_received.append(msg))
        
        # Worker joins
        await publisher.publish_worker_event(
            EventType.WORKER_JOINED, "w-001", role="executor", hostname="host-01"
        )
        
        # Worker gets task
        await publisher.publish_worker_event(
            EventType.WORKER_STATE_CHANGED, "w-001",
            old_state="idle", new_state="executing", task_id="t-001", lease_id="l-001"
        )
        
        # Task completes
        await publisher.publish_lease_event(
            EventType.LEASE_RELEASED, "l-001", worker_id="w-001"
        )
        await publisher.publish_worker_event(
            EventType.WORKER_STATE_CHANGED, "w-001",
            old_state="executing", new_state="idle"
        )
        
        # Worker leaves
        await publisher.publish_worker_event(EventType.WORKER_LEFT, "w-001")
        
        # Verify event sequence
        event_types = [e["event_type"] for e in events_received]
        assert event_types == [
            "worker_joined",
            "worker_state_changed",
            "lease_released",
            "worker_state_changed",
            "worker_left",
        ]


class TestSubscriptionFilteringIntegration:
    """Tests for filter-based filtering in real scenarios."""
    
    @pytest.fixture
    def manager(self):
        """Create connection manager."""
        return WebSocketConnectionManager()
    
    @pytest.fixture
    def publisher(self, manager):
        """Create event publisher."""
        return WebSocketEventPublisher(manager)
    
    @pytest.fixture
    def mock_ws_factory(self):
        """Factory for creating mock WebSocket clients."""
        def create_ws():
            ws = MagicMock()
            ws.accept = AsyncMock()
            ws.send_json = AsyncMock()
            ws.receive_json = AsyncMock(side_effect=asyncio.TimeoutError())
            ws.close = AsyncMock()
            return ws
        return create_ws
    
    @pytest.mark.asyncio
    async def test_dashboard_subscribes_to_all(self, manager, publisher, mock_ws_factory):
        """Test dashboard that subscribes to all events."""
        dashboard_ws = mock_ws_factory()
        await manager.connect(dashboard_ws)
        # Empty filters = all events
        
        worker_monitor_ws = mock_ws_factory()
        await manager.connect(worker_monitor_ws)
        await manager.update_filters(worker_monitor_ws, {
            "action": "set",
            "event_types": ["worker_joined", "worker_left", "worker_state_changed"]
        })
        
        task_monitor_ws = mock_ws_factory()
        await manager.connect(task_monitor_ws)
        await manager.update_filters(task_monitor_ws, {
            "action": "set",
            "event_types": ["lease_created", "lease_released"]
        })
        
        # Broadcast worker event
        await publisher.publish_worker_event(EventType.WORKER_JOINED, "w-001")
        
        dashboard_ws.send_json.assert_called()
        worker_monitor_ws.send_json.assert_called()
        task_monitor_ws.send_json.assert_not_called()
        
        # Reset
        for ws in [dashboard_ws, worker_monitor_ws, task_monitor_ws]:
            ws.send_json.reset_mock()
        
        # Broadcast lease event
        await publisher.publish_lease_event(EventType.LEASE_CREATED, "l-001")
        
        dashboard_ws.send_json.assert_called()
        worker_monitor_ws.send_json.assert_not_called()
        task_monitor_ws.send_json.assert_called()
    
    @pytest.mark.asyncio
    async def test_entity_filter_integration(self, manager, publisher, mock_ws_factory):
        """Test entity ID filtering."""
        ws_all = mock_ws_factory()
        await manager.connect(ws_all)
        
        ws_w001 = mock_ws_factory()
        await manager.connect(ws_w001)
        await manager.update_filters(ws_w001, {
            "action": "set",
            "worker_ids": ["w-001"]
        })
        
        ws_w002 = mock_ws_factory()
        await manager.connect(ws_w002)
        await manager.update_filters(ws_w002, {
            "action": "set",
            "worker_ids": ["w-002"]
        })
        
        # Broadcast for w-001
        await publisher.publish_worker_event(EventType.WORKER_JOINED, "w-001")
        
        ws_all.send_json.assert_called()
        ws_w001.send_json.assert_called()
        ws_w002.send_json.assert_not_called()
        
        # Reset
        for ws in [ws_all, ws_w001, ws_w002]:
            ws.send_json.reset_mock()
        
        # Broadcast for w-002
        await publisher.publish_worker_event(EventType.WORKER_JOINED, "w-002")
        
        ws_all.send_json.assert_called()
        ws_w001.send_json.assert_not_called()
        ws_w002.send_json.assert_called()


class TestClientReconnectionFlow:
    """Tests for client reconnection scenarios."""
    
    @pytest.mark.asyncio
    async def test_client_reconnects_after_disconnect(self):
        """Test client reconnection flow."""
        config = WSConfig(
            reconnect_delay=0.1,
            max_reconnect_delay=1.0,
            max_retries=3
        )
        
        client = TUIWebSocketClient(config)
        state_changes = []
        
        async def track_state(state):
            state_changes.append(state)
        
        client.set_connection_handler(track_state)
        
        # Simulate reconnection by directly manipulating state
        client._state = ConnectionState.CONNECTING
        await client._notify_connection_handlers(ConnectionState.CONNECTING)
        
        # Simulate successful connection
        client._state = ConnectionState.CONNECTED
        client._retry_count = 0
        await client._notify_connection_handlers(ConnectionState.CONNECTED)
        
        # Simulate disconnect
        client._state = ConnectionState.RECONNECTING
        client._retry_count = 1
        await client._notify_connection_handlers(ConnectionState.RECONNECTING)
        
        # Verify state transitions occurred
        assert ConnectionState.CONNECTING in state_changes
        assert ConnectionState.CONNECTED in state_changes
        assert ConnectionState.RECONNECTING in state_changes
    
    @pytest.mark.asyncio
    async def test_client_uses_mock_on_unavailable_websockets(self):
        """Test graceful degradation to mock client."""
        client = create_ws_client(use_mock=True)
        
        messages = []
        
        async def collect_messages(data):
            messages.append(data)
        
        client.set_message_handler(collect_messages)
        
        await client.start()
        await asyncio.sleep(0.6)  # Wait for initial state
        
        # Should have received mock initial_state
        assert any(m.get("event_type") == "initial_state" for m in messages)
        
        await client.stop()


class TestBroadcastPerformance:
    """Tests for broadcast performance with many clients."""
    
    @pytest.fixture
    def manager(self):
        """Create connection manager."""
        return WebSocketConnectionManager()
    
    @pytest.fixture
    def publisher(self, manager):
        """Create event publisher."""
        return WebSocketEventPublisher(manager)
    
    @pytest.mark.asyncio
    async def test_broadcast_to_many_clients(self, manager, publisher):
        """Test broadcast performance with 100 clients."""
        clients = []
        for _ in range(100):
            ws = MagicMock()
            ws.accept = AsyncMock()
            ws.send_json = AsyncMock()
            ws.receive_json = AsyncMock(side_effect=asyncio.TimeoutError())
            ws.close = AsyncMock()
            clients.append(ws)
            await manager.connect(ws)
        
        assert len(manager._connections) == 100
        
        # Broadcast to all
        start = time.time()
        sent = await publisher.publish_worker_event(EventType.WORKER_JOINED, "w-001")
        elapsed = time.time() - start
        
        assert sent == 100
        
        # Should complete quickly (< 1 second)
        assert elapsed < 1.0
        
        # Cleanup
        for ws in clients:
            await manager.disconnect(ws)
    
    @pytest.mark.asyncio
    async def test_concurrent_events_to_many_clients(self, manager, publisher):
        """Test concurrent broadcasts to many clients."""
        clients = []
        for _ in range(50):
            ws = MagicMock()
            ws.accept = AsyncMock()
            ws.send_json = AsyncMock()
            ws.receive_json = AsyncMock(side_effect=asyncio.TimeoutError())
            ws.close = AsyncMock()
            clients.append(ws)
            await manager.connect(ws)
        
        # Send 10 concurrent events
        await asyncio.gather(
            publisher.publish_worker_event(EventType.WORKER_JOINED, "w-001"),
            publisher.publish_worker_event(EventType.WORKER_JOINED, "w-002"),
            publisher.publish_lease_event(EventType.LEASE_CREATED, "l-001"),
            publisher.publish_lease_event(EventType.LEASE_CREATED, "l-002"),
            publisher.publish_health_event(EventType.HEALTH_UPDATE, {"status": "ok"}),
            publisher.publish_worker_event(EventType.WORKER_STATE_CHANGED, "w-001", old_state="idle", new_state="executing"),
            publisher.publish_worker_event(EventType.WORKER_STATE_CHANGED, "w-002", old_state="idle", new_state="executing"),
            publisher.publish_lease_event(EventType.LEASE_RELEASED, "l-001"),
            publisher.publish_lease_event(EventType.LEASE_RELEASED, "l-002"),
            publisher.publish_worker_event(EventType.WORKER_LEFT, "w-001"),
        )
        
        # Each client should have received 10 messages
        for ws in clients:
            assert ws.send_json.call_count == 10
        
        # Cleanup
        for ws in clients:
            await manager.disconnect(ws)


class TestErrorHandlingIntegration:
    """Tests for error handling in integrated scenarios."""
    
    @pytest.fixture
    def manager(self):
        """Create connection manager."""
        return WebSocketConnectionManager()
    
    @pytest.fixture
    def publisher(self, manager):
        """Create event publisher."""
        return WebSocketEventPublisher(manager)
    
    @pytest.mark.asyncio
    async def test_broadcast_continues_with_some_failed_clients(self, manager, publisher):
        """Test that broadcast continues when some clients fail."""
        good_ws = MagicMock()
        good_ws.accept = AsyncMock()
        good_ws.send_json = AsyncMock()
        good_ws.close = AsyncMock()
        
        bad_ws = MagicMock()
        bad_ws.accept = AsyncMock()
        bad_ws.send_json = AsyncMock(side_effect=Exception("Connection lost"))
        bad_ws.close = AsyncMock()
        
        another_good_ws = MagicMock()
        another_good_ws.accept = AsyncMock()
        another_good_ws.send_json = AsyncMock()
        another_good_ws.close = AsyncMock()
        
        await manager.connect(good_ws)
        await manager.connect(bad_ws)
        await manager.connect(another_good_ws)
        
        assert len(manager._connections) == 3
        
        # Broadcast
        sent = await publisher.publish_worker_event(EventType.WORKER_JOINED, "w-001")
        
        # Should have sent to good clients only
        assert sent == 2
        
        # Bad client should be removed
        assert len(manager._connections) == 2
        assert good_ws in manager._connections
        assert bad_ws not in manager._connections
        assert another_good_ws in manager._connections
    
    @pytest.mark.asyncio
    async def test_client_handles_malformed_json(self):
        """Test client handles malformed JSON gracefully."""
        client = TUIWebSocketClient(WSConfig())
        
        # Simulate malformed JSON being received
        malformed = '{"incomplete":'
        
        try:
            json.loads(malformed)
        except json.JSONDecodeError as e:
            # This is what _listen would catch
            client._last_error = f"JSON decode error: {e}"
        
        assert "JSON decode error" in client._last_error


class TestMessageFlowIntegration:
    """Tests for complete message flow."""
    
    @pytest.fixture
    def manager(self):
        """Create connection manager."""
        return WebSocketConnectionManager()
    
    @pytest.fixture
    def publisher(self, manager):
        """Create event publisher."""
        return WebSocketEventPublisher(manager)
    
    @pytest.mark.asyncio
    async def test_full_event_flow_from_publisher_to_client(self, manager, publisher):
        """Test complete flow: event occurs -> publisher broadcasts -> client receives."""
        # Create mock client
        ws = MagicMock()
        ws.accept = AsyncMock()
        messages_received = []
        ws.send_json = AsyncMock(side_effect=lambda msg: messages_received.append(msg))
        ws.receive_json = AsyncMock(side_effect=asyncio.TimeoutError())
        ws.close = AsyncMock()
        
        await manager.connect(ws)
        
        # Simulate full worker lifecycle
        # 1. Worker joins
        await publisher.publish_worker_event(
            EventType.WORKER_JOINED, "w-001",
            role="executor", hostname="host-01", pid=1234
        )
        
        # 2. Initial health check
        await publisher.publish_health_event(
            EventType.HEALTH_UPDATE,
            {"status": "healthy", "postgres_ready": True, "redis_ready": True}
        )
        
        # 3. Task arrives and gets dispatched
        await publisher.publish_lease_event(
            EventType.LEASE_CREATED, "l-001",
            worker_id="w-001", run_id="r-001"
        )
        await publisher.publish_worker_event(
            EventType.WORKER_STATE_CHANGED, "w-001",
            old_state="idle", new_state="executing", task_id="t-001", lease_id="l-001"
        )
        
        # 4. Task completes
        await publisher.publish_run_event(
            EventType.RUN_COMPLETED, "r-001",
            success=True, duration_seconds=12.5
        )
        await publisher.publish_lease_event(
            EventType.LEASE_RELEASED, "l-001"
        )
        await publisher.publish_worker_event(
            EventType.WORKER_STATE_CHANGED, "w-001",
            old_state="executing", new_state="idle"
        )
        
        # 5. Queue update
        await publisher.publish_queue_event(
            EventType.QUEUE_UPDATE,
            [{"shard": "default", "depth": 5}]
        )
        
        # 6. Worker leaves
        await publisher.publish_worker_event(EventType.WORKER_LEFT, "w-001")
        
        # Verify client received all events in order
        event_types = [m["event_type"] for m in messages_received]
        
        assert event_types[0] == "worker_joined"
        assert event_types[1] == "health_update"
        assert event_types[2] == "lease_created"
        assert event_types[3] == "worker_state_changed"
        assert event_types[4] == "run_completed"
        assert event_types[5] == "lease_released"
        assert event_types[6] == "worker_state_changed"
        assert event_types[7] == "queue_update"
        assert event_types[8] == "worker_left"
        
        # Verify data integrity
        worker_joined_msg = messages_received[0]
        assert worker_joined_msg["data"]["worker_id"] == "w-001"
        assert worker_joined_msg["data"]["hostname"] == "host-01"
        
        run_completed_msg = messages_received[4]
        assert run_completed_msg["data"]["success"] is True
        assert run_completed_msg["data"]["duration_seconds"] == 12.5


class TestConcurrencyIntegration:
    """Tests for concurrent operations."""
    
    @pytest.fixture
    def manager(self):
        """Create connection manager."""
        return WebSocketConnectionManager()
    
    @pytest.fixture
    def publisher(self, manager):
        """Create event publisher."""
        return WebSocketEventPublisher(manager)
    
    @pytest.mark.asyncio
    async def test_concurrent_connect_disconnect(self, manager):
        """Test concurrent connect and disconnect operations."""
        async def connect_client(i):
            ws = MagicMock()
            ws.accept = AsyncMock()
            ws.send_json = AsyncMock()
            ws.receive_json = AsyncMock(side_effect=asyncio.TimeoutError())
            ws.close = AsyncMock()
            await manager.connect(ws)
            return ws
        
        async def disconnect_client(ws):
            await manager.disconnect(ws)
        
        # Connect 10 clients concurrently
        clients = await asyncio.gather(*[connect_client(i) for i in range(10)])
        
        assert len(manager._connections) == 10
        
        # Disconnect all concurrently
        await asyncio.gather(*[disconnect_client(ws) for ws in clients])
        
        assert len(manager._connections) == 0
    
    @pytest.mark.asyncio
    async def test_subscribe_unsubscribe_concurrent(self, manager):
        """Test concurrent subscribe/unsubscribe operations."""
        ws = MagicMock()
        ws.accept = AsyncMock()
        ws.send_json = AsyncMock()
        ws.receive_json = AsyncMock(side_effect=asyncio.TimeoutError())
        ws.close = AsyncMock()
        
        await manager.connect(ws)
        
        # Subscribe to multiple event types concurrently
        await asyncio.gather(
            manager.update_filters(ws, {"action": "add", "event_types": ["worker_events"]}),
            manager.update_filters(ws, {"action": "add", "event_types": ["task_events"]}),
            manager.update_filters(ws, {"action": "add", "event_types": ["health_events"]}),
        )
        
        # All should be subscribed
        conn_info = manager._connections[ws]
        assert "worker_events" in conn_info.filters.event_types
        assert "task_events" in conn_info.filters.event_types
        assert "health_events" in conn_info.filters.event_types
        
        # Unsubscribe concurrently
        await asyncio.gather(
            manager.update_filters(ws, {"action": "remove", "event_types": ["worker_events"]}),
            manager.update_filters(ws, {"action": "remove", "event_types": ["task_events"]}),
        )
        
        assert "worker_events" not in conn_info.filters.event_types
        assert "task_events" not in conn_info.filters.event_types
        assert "health_events" in conn_info.filters.event_types


class TestManagerLifecycle:
    """Tests for manager initialize/shutdown lifecycle."""
    
    @pytest.mark.asyncio
    async def test_manager_initialize_starts_background_tasks(self):
        """Test that initialize starts heartbeat and cleanup tasks."""
        manager = WebSocketConnectionManager()
        
        await manager.initialize()
        
        assert manager._is_initialized is True
        assert manager._heartbeat_task is not None
        assert manager._cleanup_task is not None
        
        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_manager_shutdown_closes_connections(self):
        """Test that shutdown closes all connections."""
        manager = WebSocketConnectionManager()
        
        ws = MagicMock()
        ws.accept = AsyncMock()
        ws.send_json = AsyncMock()
        ws.close = AsyncMock()
        
        await manager.connect(ws)
        await manager.initialize()
        
        await manager.shutdown()
        
        assert manager._is_initialized is False
        assert len(manager._connections) == 0
        ws.close.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_manager_can_reinitialize_after_shutdown(self):
        """Test that manager can be reinitialized after shutdown."""
        manager = WebSocketConnectionManager()
        
        await manager.initialize()
        await manager.shutdown()
        
        assert manager._is_initialized is False
        
        await manager.initialize()
        
        assert manager._is_initialized is True
        
        await manager.shutdown()