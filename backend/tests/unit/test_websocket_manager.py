"""
Unit tests for WebSocket ConnectionManager.

Tests cover:
- Connection lifecycle (connect/disconnect)
- Subscription management (filter-based routing)
- Broadcast to multiple clients
- Connection health tracking
- Heartbeat handling
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

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
    get_manager,
    get_publisher,
)


class TestConnectionFilters:
    """Tests for ConnectionFilters dataclass."""
    
    def test_empty_filters_matches_all(self):
        """Test that empty filters match all events."""
        filters = ConnectionFilters()
        
        assert filters.matches("worker_joined", {"worker_id": "w-001"}) is True
        assert filters.matches("task_dispatched", {"task_id": "t-001"}) is True
        assert filters.matches("any_event", {}) is True
    
    def test_event_type_filter(self):
        """Test event type filtering."""
        filters = ConnectionFilters(event_types={"worker_joined", "worker_left"})
        
        assert filters.matches("worker_joined", {}) is True
        assert filters.matches("worker_left", {}) is True
        assert filters.matches("task_dispatched", {}) is False
    
    def test_worker_id_filter(self):
        """Test worker_id filtering."""
        filters = ConnectionFilters(worker_ids={"w-001", "w-002"})
        
        assert filters.matches("worker_joined", {"worker_id": "w-001"}) is True
        assert filters.matches("worker_state_changed", {"worker_id": "w-002"}) is True
        assert filters.matches("worker_joined", {"worker_id": "w-003"}) is False
    
    def test_lease_id_filter(self):
        """Test lease_id filtering."""
        filters = ConnectionFilters(lease_ids={"l-001", "l-002"})
        
        assert filters.matches("lease_created", {"lease_id": "l-001"}) is True
        assert filters.matches("lease_released", {"lease_id": "l-002"}) is True
        assert filters.matches("lease_created", {"lease_id": "l-003"}) is False
    
    def test_multiple_filters(self):
        """Test combined filters."""
        filters = ConnectionFilters(
            worker_ids={"w-001"},
            event_types={"worker_joined", "worker_state_changed"}
        )
        
        # Matches event_type but not worker_id
        assert filters.matches("worker_joined", {"worker_id": "w-002"}) is False
        # Matches both
        assert filters.matches("worker_joined", {"worker_id": "w-001"}) is True
        # Wrong event_type
        assert filters.matches("task_dispatched", {"worker_id": "w-001"}) is False


class TestWebSocketConnectionManager:
    """Tests for WebSocketConnectionManager class."""
    
    @pytest.fixture
    def manager(self):
        """Create a fresh WebSocketConnectionManager for each test."""
        return WebSocketConnectionManager()
    
    @pytest.fixture
    def mock_websocket(self):
        """Create a mock WebSocket."""
        ws = MagicMock()
        ws.accept = AsyncMock()
        ws.send_json = AsyncMock()
        ws.receive_json = AsyncMock()
        ws.close = AsyncMock()
        return ws
    
    @pytest.mark.asyncio
    async def test_connect(self, manager, mock_websocket):
        """Test connection registration."""
        conn_id = await manager.connect(mock_websocket)
        
        # Verify accept was called
        mock_websocket.accept.assert_called_once()
        
        # Verify connection ID format
        assert conn_id.startswith("ws-")
        assert len(manager._connections) == 1
    
    @pytest.mark.asyncio
    async def test_connect_with_endpoint_type(self, manager, mock_websocket):
        """Test connection with specific endpoint type."""
        conn_id = await manager.connect(mock_websocket, endpoint_type="workers")
        
        assert "workers" in conn_id
        conn_info = manager._connections[mock_websocket]
        assert conn_info.endpoint_type == "workers"
    
    @pytest.mark.asyncio
    async def test_connect_with_filters(self, manager, mock_websocket):
        """Test connection with initial filters."""
        initial_filters = {
            "worker_ids": ["w-001", "w-002"],
            "event_types": ["worker_joined"]
        }
        conn_id = await manager.connect(mock_websocket, initial_filters=initial_filters)
        
        conn_info = manager._connections[mock_websocket]
        assert "w-001" in conn_info.filters.worker_ids
        assert "w-002" in conn_info.filters.worker_ids
        assert "worker_joined" in conn_info.filters.event_types
    
    @pytest.mark.asyncio
    async def test_connect_multiple(self, manager):
        """Test multiple connections get unique IDs."""
        ws1 = MagicMock()
        ws1.accept = AsyncMock()
        ws2 = MagicMock()
        ws2.accept = AsyncMock()
        ws3 = MagicMock()
        ws3.accept = AsyncMock()
        
        id1 = await manager.connect(ws1)
        id2 = await manager.connect(ws2)
        id3 = await manager.connect(ws3)
        
        # All IDs should be unique
        assert len({id1, id2, id3}) == 3
        assert len(manager._connections) == 3
    
    @pytest.mark.asyncio
    async def test_disconnect(self, manager, mock_websocket):
        """Test connection removal."""
        conn_id = await manager.connect(mock_websocket)
        assert len(manager._connections) == 1
        
        await manager.disconnect(mock_websocket)
        
        assert len(manager._connections) == 0
        assert mock_websocket not in manager._connections
    
    @pytest.mark.asyncio
    async def test_disconnect_nonexistent(self, manager):
        """Test disconnecting a non-existent connection doesn't error."""
        fake_ws = MagicMock()
        # Should not raise
        await manager.disconnect(fake_ws)
        assert len(manager._connections) == 0
    
    @pytest.mark.asyncio
    async def test_update_filters_set(self, manager, mock_websocket):
        """Test setting filters."""
        await manager.connect(mock_websocket)
        
        await manager.update_filters(mock_websocket, {
            "action": "set",
            "worker_ids": ["w-001"],
            "event_types": ["worker_joined"]
        })
        
        conn_info = manager._connections[mock_websocket]
        assert conn_info.filters.worker_ids == {"w-001"}
        assert conn_info.filters.event_types == {"worker_joined"}
    
    @pytest.mark.asyncio
    async def test_update_filters_add(self, manager, mock_websocket):
        """Test adding filters."""
        await manager.connect(mock_websocket)
        
        # Set initial
        await manager.update_filters(mock_websocket, {
            "action": "set",
            "worker_ids": ["w-001"]
        })
        
        # Add more
        await manager.update_filters(mock_websocket, {
            "action": "add",
            "worker_ids": ["w-002"]
        })
        
        conn_info = manager._connections[mock_websocket]
        assert conn_info.filters.worker_ids == {"w-001", "w-002"}
    
    @pytest.mark.asyncio
    async def test_update_filters_remove(self, manager, mock_websocket):
        """Test removing filters."""
        await manager.connect(mock_websocket)
        
        # Set initial
        await manager.update_filters(mock_websocket, {
            "action": "set",
            "worker_ids": ["w-001", "w-002"]
        })
        
        # Remove one
        await manager.update_filters(mock_websocket, {
            "action": "remove",
            "worker_ids": ["w-001"]
        })
        
        conn_info = manager._connections[mock_websocket]
        assert conn_info.filters.worker_ids == {"w-002"}
    
    @pytest.mark.asyncio
    async def test_update_filters_clear(self, manager, mock_websocket):
        """Test clearing filters."""
        await manager.connect(mock_websocket)
        
        # Set initial
        await manager.update_filters(mock_websocket, {
            "action": "set",
            "worker_ids": ["w-001"],
            "event_types": ["worker_joined"]
        })
        
        # Clear all
        await manager.update_filters(mock_websocket, {"action": "clear"})
        
        conn_info = manager._connections[mock_websocket]
        assert len(conn_info.filters.worker_ids) == 0
        assert len(conn_info.filters.event_types) == 0
    
    @pytest.mark.asyncio
    async def test_should_send_filter_match(self, manager, mock_websocket):
        """Test _should_send with matching filter."""
        await manager.connect(mock_websocket)
        await manager.update_filters(mock_websocket, {
            "action": "set",
            "worker_ids": ["w-001"]
        })
        
        conn_info = manager._connections[mock_websocket]
        
        # Matching worker_id
        assert manager._should_send(conn_info, "worker_joined", {"worker_id": "w-001"}) is True
        # Non-matching worker_id
        assert manager._should_send(conn_info, "worker_joined", {"worker_id": "w-002"}) is False
    
    @pytest.mark.asyncio
    async def test_should_send_empty_filters(self, manager, mock_websocket):
        """Test _should_send with empty filters (should receive all)."""
        await manager.connect(mock_websocket)
        
        conn_info = manager._connections[mock_websocket]
        
        assert manager._should_send(conn_info, "any_event", {}) is True
        assert manager._should_send(conn_info, "worker_joined", {"worker_id": "w-001"}) is True
    
    @pytest.mark.asyncio
    async def test_broadcast_all(self, manager):
        """Test broadcasting to all connected clients."""
        # Create multiple mock websockets
        websockets = []
        for i in range(3):
            ws = MagicMock()
            ws.accept = AsyncMock()
            ws.send_json = AsyncMock()
            websockets.append(ws)
            await manager.connect(ws)
        
        sent_count = await manager.broadcast("test_event", {"data": "hello"})
        
        assert sent_count == 3
        for ws in websockets:
            ws.send_json.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_broadcast_filtered(self, manager):
        """Test broadcast respects filters."""
        ws1 = MagicMock()
        ws1.accept = AsyncMock()
        ws1.send_json = AsyncMock()
        
        ws2 = MagicMock()
        ws2.accept = AsyncMock()
        ws2.send_json = AsyncMock()
        
        await manager.connect(ws1)
        await manager.connect(ws2)
        
        # Filter ws1 to only receive worker_joined events
        await manager.update_filters(ws1, {
            "action": "set",
            "event_types": ["worker_joined"]
        })
        
        # Broadcast worker_joined
        sent1 = await manager.broadcast("worker_joined", {"worker_id": "w-001"})
        
        # Both should receive (ws2 has empty filters)
        assert sent1 == 2
        
        # Broadcast different event
        ws1.send_json.reset_mock()
        ws2.send_json.reset_mock()
        
        sent2 = await manager.broadcast("task_dispatched", {"task_id": "t-001"})
        
        # Only ws2 should receive
        assert sent2 == 1
        ws1.send_json.assert_not_called()
        ws2.send_json.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_broadcast_with_entity_filter(self, manager):
        """Test broadcast with entity ID filters."""
        ws1 = MagicMock()
        ws1.accept = AsyncMock()
        ws1.send_json = AsyncMock()
        
        ws2 = MagicMock()
        ws2.accept = AsyncMock()
        ws2.send_json = AsyncMock()
        
        await manager.connect(ws1)
        await manager.connect(ws2)
        
        # Filter ws1 to only w-001
        await manager.update_filters(ws1, {
            "action": "set",
            "worker_ids": ["w-001"]
        })
        
        # Broadcast for w-001
        sent1 = await manager.broadcast("worker_state_changed", {"worker_id": "w-001"}, {"worker_id": "w-001"})
        
        # ws1 should match, ws2 (empty) should receive all
        assert sent1 == 2
        
        # Broadcast for w-002
        ws1.send_json.reset_mock()
        ws2.send_json.reset_mock()
        
        sent2 = await manager.broadcast("worker_state_changed", {"worker_id": "w-002"}, {"worker_id": "w-002"})
        
        # Only ws2 should receive
        assert sent2 == 1
        ws1.send_json.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_broadcast_handles_disconnect(self, manager):
        """Test that disconnected clients are cleaned up during broadcast."""
        ws1 = MagicMock()
        ws1.accept = AsyncMock()
        ws1.send_json = AsyncMock()
        
        ws2 = MagicMock()
        ws2.accept = AsyncMock()
        ws2.send_json = AsyncMock(side_effect=Exception("Connection lost"))
        
        await manager.connect(ws1)
        await manager.connect(ws2)
        
        assert len(manager._connections) == 2
        
        sent_count = await manager.broadcast("test_event", {"data": "hello"})
        
        # Only ws1 should receive (ws2 errored)
        assert sent_count == 1
        assert len(manager._connections) == 1
        assert ws1 in manager._connections
        assert ws2 not in manager._connections
    
    @pytest.mark.asyncio
    async def test_send_to(self, manager, mock_websocket):
        """Test sending to specific connection."""
        await manager.connect(mock_websocket)
        
        result = await manager.send_to(
            mock_websocket,
            "test_event",
            {"data": "hello"}
        )
        
        assert result is True
        mock_websocket.send_json.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_send_to_failure(self, manager, mock_websocket):
        """Test send_to handles failure."""
        await manager.connect(mock_websocket)
        mock_websocket.send_json = AsyncMock(side_effect=Exception("Send failed"))
        
        result = await manager.send_to(
            mock_websocket,
            "test_event",
            {"data": "hello"}
        )
        
        assert result is False
    
    @pytest.mark.asyncio
    async def test_connection_count(self, manager, mock_websocket):
        """Test connection_count property."""
        assert len(manager._connections) == 0
        
        await manager.connect(mock_websocket)
        assert len(manager._connections) == 1
        
        ws2 = MagicMock()
        ws2.accept = AsyncMock()
        await manager.connect(ws2)
        assert len(manager._connections) == 2
        
        await manager.disconnect(mock_websocket)
        assert len(manager._connections) == 1
    
    @pytest.mark.asyncio
    async def test_get_connection_info(self, manager, mock_websocket):
        """Test get_connection_info returns correct metadata."""
        await manager.connect(mock_websocket)
        
        # get_connection_info returns a list of all connections
        info_list = manager.get_connection_info()
        
        assert len(info_list) == 1
        assert "connection_id" in info_list[0]
        assert info_list[0]["endpoint_type"] == "general"
        assert info_list[0]["connected_at"] > 0
    
    @pytest.mark.asyncio
    async def test_get_connection_info_empty(self, manager):
        """Test get_connection_info when no connections."""
        info_list = manager.get_connection_info()
        
        assert len(info_list) == 0
    
    @pytest.mark.asyncio
    async def test_initialize_starts_background_tasks(self, manager):
        """Test that initialize starts heartbeat and cleanup tasks."""
        await manager.initialize()
        
        assert manager._is_initialized is True
        assert manager._heartbeat_task is not None
        assert manager._cleanup_task is not None
        
        # Clean up
        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_shutdown_cleans_up(self, manager, mock_websocket):
        """Test shutdown closes all connections and stops tasks."""
        await manager.connect(mock_websocket)
        await manager.initialize()
        
        await manager.shutdown()
        
        assert manager._is_initialized is False
        assert len(manager._connections) == 0
        mock_websocket.close.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_connection_limit(self, manager):
        """Test connection limit enforcement."""
        # Patch the MAX_CONNECTIONS constant for this test
        with patch('harness_lab.control_plane.websocket.MAX_CONNECTIONS', 3):
            ws1 = MagicMock()
            ws1.accept = AsyncMock()
            ws1.close = AsyncMock()
            ws2 = MagicMock()
            ws2.accept = AsyncMock()
            ws2.close = AsyncMock()
            ws3 = MagicMock()
            ws3.accept = AsyncMock()
            ws3.close = AsyncMock()
            
            ws4 = MagicMock()
            ws4.accept = AsyncMock()
            ws4.close = AsyncMock()
            
            await manager.connect(ws1)
            await manager.connect(ws2)
            await manager.connect(ws3)
            
            # Fourth connection should fail
            with pytest.raises(ValueError, match="Maximum connections"):
                await manager.connect(ws4)


class TestWebSocketEventPublisher:
    """Tests for WebSocketEventPublisher."""
    
    @pytest.fixture
    def manager(self):
        """Create a manager."""
        return WebSocketConnectionManager()
    
    @pytest.fixture
    def publisher(self, manager):
        """Create a publisher with the manager."""
        return WebSocketEventPublisher(manager)
    
    @pytest.fixture
    def mock_websocket(self):
        """Create a mock WebSocket."""
        ws = MagicMock()
        ws.accept = AsyncMock()
        ws.send_json = AsyncMock()
        ws.receive_json = AsyncMock()
        ws.close = AsyncMock()
        return ws
    
    @pytest.mark.asyncio
    async def test_publish_worker_event(self, publisher, manager, mock_websocket):
        """Test publishing worker event."""
        await manager.connect(mock_websocket)
        
        sent = await publisher.publish_worker_event(
            EventType.WORKER_JOINED,
            "w-001",
            role="executor",
            hostname="host-01"
        )
        
        assert sent == 1
        mock_websocket.send_json.assert_called_once()
        
        call_args = mock_websocket.send_json.call_args[0][0]
        assert call_args["event_type"] == "worker_joined"
        assert call_args["data"]["worker_id"] == "w-001"
        assert call_args["data"]["role"] == "executor"
    
    @pytest.mark.asyncio
    async def test_publish_lease_event(self, publisher, manager, mock_websocket):
        """Test publishing lease event."""
        await manager.connect(mock_websocket)
        
        sent = await publisher.publish_lease_event(
            EventType.LEASE_CREATED,
            "l-001",
            worker_id="w-001",
            run_id="r-001"
        )
        
        assert sent == 1
        
        call_args = mock_websocket.send_json.call_args[0][0]
        assert call_args["event_type"] == "lease_created"
        assert call_args["data"]["lease_id"] == "l-001"
        assert call_args["data"]["worker_id"] == "w-001"
        assert call_args["data"]["run_id"] == "r-001"
    
    @pytest.mark.asyncio
    async def test_publish_queue_event(self, publisher, manager, mock_websocket):
        """Test publishing queue event."""
        await manager.connect(mock_websocket)
        
        queue_data = {
            "shard": "default",
            "depth": 5,
            "sample_tasks": ["t-001", "t-002"]
        }
        
        sent = await publisher.publish_queue_event(EventType.QUEUE_UPDATE, queue_data)
        
        assert sent == 1
        
        call_args = mock_websocket.send_json.call_args[0][0]
        assert call_args["event_type"] == "queue_update"
        assert call_args["data"] == queue_data
    
    @pytest.mark.asyncio
    async def test_publish_health_event(self, publisher, manager, mock_websocket):
        """Test publishing health event."""
        await manager.connect(mock_websocket)
        
        health_data = {
            "status": "healthy",
            "postgres_ready": True,
            "redis_ready": True
        }
        
        sent = await publisher.publish_health_event(EventType.HEALTH_UPDATE, health_data)
        
        assert sent == 1
        
        call_args = mock_websocket.send_json.call_args[0][0]
        assert call_args["event_type"] == "health_update"
        assert call_args["data"] == health_data
    
    @pytest.mark.asyncio
    async def test_publish_run_event(self, publisher, manager, mock_websocket):
        """Test publishing run event."""
        await manager.connect(mock_websocket)
        
        sent = await publisher.publish_run_event(
            EventType.RUN_STARTED,
            "r-001",
            worker_id="w-001"
        )
        
        assert sent == 1
        
        call_args = mock_websocket.send_json.call_args[0][0]
        assert call_args["event_type"] == "run_started"
        assert call_args["data"]["run_id"] == "r-001"
    
    @pytest.mark.asyncio
    async def test_publish_system_event(self, publisher, manager, mock_websocket):
        """Test publishing system event."""
        await manager.connect(mock_websocket)
        
        sent = await publisher.publish_system_event(
            event_name="config_reload",
            message="Configuration reloaded",
            level="info",
            details={"version": "v2.1"}
        )
        
        assert sent == 1
        
        call_args = mock_websocket.send_json.call_args[0][0]
        assert call_args["event_type"] == "system_event"
        assert call_args["data"]["event_name"] == "config_reload"
        assert call_args["data"]["message"] == "Configuration reloaded"
        assert call_args["data"]["level"] == "info"


class TestGlobalInstances:
    """Tests for global manager and publisher instances."""
    
    def test_get_manager_returns_singleton(self):
        """Test that get_manager returns the same instance."""
        # Reset global
        import harness_lab.control_plane.websocket as ws_module
        ws_module._manager = None
        
        manager1 = get_manager()
        manager2 = get_manager()
        
        assert manager1 == manager2
    
    def test_get_publisher_returns_singleton(self):
        """Test that get_publisher returns the same instance."""
        import harness_lab.control_plane.websocket as ws_module
        ws_module._publisher = None
        ws_module._manager = None
        
        publisher1 = get_publisher()
        publisher2 = get_publisher()
        
        assert publisher1 == publisher2
    
    def test_get_publisher_uses_get_manager(self):
        """Test that get_publisher uses get_manager."""
        import harness_lab.control_plane.websocket as ws_module
        ws_module._publisher = None
        ws_module._manager = None
        
        publisher = get_publisher()
        
        assert publisher._manager == get_manager()


class TestEventTypeEnum:
    """Tests for EventType enum."""
    
    def test_event_type_values(self):
        """Test EventType enum values."""
        assert EventType.WORKER_JOINED == "worker_joined"
        assert EventType.WORKER_LEFT == "worker_left"
        assert EventType.TASK_DISPATCHED == "task_dispatched"
        assert EventType.HEALTH_UPDATE == "health_update"
        assert EventType.SYSTEM_EVENT == "system_event"
    
    def test_event_type_string_conversion(self):
        """Test EventType can be used as string."""
        event = EventType.WORKER_JOINED
        # In Python 3.12, str(event) returns the full name
        assert event.value == "worker_joined"