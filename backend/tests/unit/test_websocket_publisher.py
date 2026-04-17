"""
Unit tests for WebSocket Event Publisher functions.

Tests cover:
- Worker events (join, leave, state change, drain, resume)
- Lease events (created, released, expired)
- Queue events
- Health events
- Run events
- System events
"""

from __future__ import annotations

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
    EventType,
)


class TestPublishWorkerEvents:
    """Tests for worker-related publish methods."""
    
    @pytest.fixture
    def manager(self):
        """Create manager."""
        return WebSocketConnectionManager()
    
    @pytest.fixture
    def publisher(self, manager):
        """Create publisher."""
        return WebSocketEventPublisher(manager)
    
    @pytest.fixture
    def mock_ws(self):
        """Create mock WebSocket."""
        ws = MagicMock()
        ws.accept = AsyncMock()
        ws.send_json = AsyncMock()
        ws.close = AsyncMock()
        return ws
    
    @pytest.mark.asyncio
    async def test_publish_worker_joined(self, publisher, manager, mock_ws):
        """Test publishing worker_joined event."""
        await manager.connect(mock_ws)
        
        sent = await publisher.publish_worker_event(
            EventType.WORKER_JOINED,
            "w-001",
            role="executor",
            hostname="host-01",
            pid=12345
        )
        
        assert sent == 1
        msg = mock_ws.send_json.call_args[0][0]
        
        assert msg["event_type"] == "worker_joined"
        assert msg["data"]["worker_id"] == "w-001"
        assert msg["data"]["role"] == "executor"
        assert msg["data"]["hostname"] == "host-01"
        assert msg["data"]["pid"] == 12345
        assert "timestamp" in msg
        assert "sequence" in msg
    
    @pytest.mark.asyncio
    async def test_publish_worker_left(self, publisher, manager, mock_ws):
        """Test publishing worker_left event."""
        await manager.connect(mock_ws)
        
        sent = await publisher.publish_worker_event(
            EventType.WORKER_LEFT,
            "w-001"
        )
        
        assert sent == 1
        msg = mock_ws.send_json.call_args[0][0]
        
        assert msg["event_type"] == "worker_left"
        assert msg["data"]["worker_id"] == "w-001"
    
    @pytest.mark.asyncio
    async def test_publish_worker_state_changed(self, publisher, manager, mock_ws):
        """Test publishing worker_state_changed event."""
        await manager.connect(mock_ws)
        
        sent = await publisher.publish_worker_event(
            EventType.WORKER_STATE_CHANGED,
            "w-001",
            old_state="idle",
            new_state="executing",
            task_id="t-123",
            lease_id="l-456"
        )
        
        assert sent == 1
        msg = mock_ws.send_json.call_args[0][0]
        
        assert msg["event_type"] == "worker_state_changed"
        assert msg["data"]["worker_id"] == "w-001"
        assert msg["data"]["old_state"] == "idle"
        assert msg["data"]["new_state"] == "executing"
        assert msg["data"]["task_id"] == "t-123"
        assert msg["data"]["lease_id"] == "l-456"
    
    @pytest.mark.asyncio
    async def test_publish_worker_drain(self, publisher, manager, mock_ws):
        """Test publishing worker_drain event."""
        await manager.connect(mock_ws)
        
        sent = await publisher.publish_worker_event(
            EventType.WORKER_DRAIN,
            "w-001",
            reason="maintenance",
            initiator="admin"
        )
        
        assert sent == 1
        msg = mock_ws.send_json.call_args[0][0]
        
        assert msg["event_type"] == "worker_drain"
        assert msg["data"]["worker_id"] == "w-001"
        assert msg["data"]["reason"] == "maintenance"
        assert msg["data"]["initiator"] == "admin"
    
    @pytest.mark.asyncio
    async def test_publish_worker_resume(self, publisher, manager, mock_ws):
        """Test publishing worker_resume event."""
        await manager.connect(mock_ws)
        
        sent = await publisher.publish_worker_event(
            EventType.WORKER_RESUME,
            "w-001"
        )
        
        assert sent == 1
        msg = mock_ws.send_json.call_args[0][0]
        
        assert msg["event_type"] == "worker_resume"
        assert msg["data"]["worker_id"] == "w-001"


class TestPublishLeaseEvents:
    """Tests for lease-related publish methods."""
    
    @pytest.fixture
    def manager(self):
        """Create manager."""
        return WebSocketConnectionManager()
    
    @pytest.fixture
    def publisher(self, manager):
        """Create publisher."""
        return WebSocketEventPublisher(manager)
    
    @pytest.fixture
    def mock_ws(self):
        """Create mock WebSocket."""
        ws = MagicMock()
        ws.accept = AsyncMock()
        ws.send_json = AsyncMock()
        ws.close = AsyncMock()
        return ws
    
    @pytest.mark.asyncio
    async def test_publish_lease_created(self, publisher, manager, mock_ws):
        """Test publishing lease_created event."""
        await manager.connect(mock_ws)
        
        sent = await publisher.publish_lease_event(
            EventType.LEASE_CREATED,
            "l-001",
            worker_id="w-001",
            run_id="r-001"
        )
        
        assert sent == 1
        msg = mock_ws.send_json.call_args[0][0]
        
        assert msg["event_type"] == "lease_created"
        assert msg["data"]["lease_id"] == "l-001"
        assert msg["data"]["worker_id"] == "w-001"
        assert msg["data"]["run_id"] == "r-001"
    
    @pytest.mark.asyncio
    async def test_publish_lease_released(self, publisher, manager, mock_ws):
        """Test publishing lease_released event."""
        await manager.connect(mock_ws)
        
        sent = await publisher.publish_lease_event(
            EventType.LEASE_RELEASED,
            "l-001",
            worker_id="w-001"
        )
        
        assert sent == 1
        msg = mock_ws.send_json.call_args[0][0]
        
        assert msg["event_type"] == "lease_released"
        assert msg["data"]["lease_id"] == "l-001"
    
    @pytest.mark.asyncio
    async def test_publish_lease_expired(self, publisher, manager, mock_ws):
        """Test publishing lease_expired event."""
        await manager.connect(mock_ws)
        
        sent = await publisher.publish_lease_event(
            EventType.LEASE_EXPIRED,
            "l-001",
            reason="timeout"
        )
        
        assert sent == 1
        msg = mock_ws.send_json.call_args[0][0]
        
        assert msg["event_type"] == "lease_expired"
        assert msg["data"]["lease_id"] == "l-001"
        assert msg["data"]["reason"] == "timeout"


class TestPublishQueueEvents:
    """Tests for queue-related publish methods."""
    
    @pytest.fixture
    def manager(self):
        """Create manager."""
        return WebSocketConnectionManager()
    
    @pytest.fixture
    def publisher(self, manager):
        """Create publisher."""
        return WebSocketEventPublisher(manager)
    
    @pytest.fixture
    def mock_ws(self):
        """Create mock WebSocket."""
        ws = MagicMock()
        ws.accept = AsyncMock()
        ws.send_json = AsyncMock()
        ws.close = AsyncMock()
        return ws
    
    @pytest.mark.asyncio
    async def test_publish_queue_update(self, publisher, manager, mock_ws):
        """Test publishing queue_update event."""
        await manager.connect(mock_ws)
        
        queue_data = {
            "shard": "default",
            "depth": 15,
            "oldest_task_age": 120.5,
            "sample_tasks": ["T-001", "T-002", "T-003"]
        }
        
        sent = await publisher.publish_queue_event(EventType.QUEUE_UPDATE, queue_data)
        
        assert sent == 1
        msg = mock_ws.send_json.call_args[0][0]
        
        assert msg["event_type"] == "queue_update"
        assert msg["data"] == queue_data
    
    @pytest.mark.asyncio
    async def test_publish_queue_shard_update(self, publisher, manager, mock_ws):
        """Test publishing queue_shard_update event."""
        await manager.connect(mock_ws)
        
        shard_data = {
            "shard_id": "priority",
            "depth": 3,
            "max_depth": 100
        }
        
        sent = await publisher.publish_queue_event(EventType.QUEUE_SHARD_UPDATE, shard_data)
        
        assert sent == 1
        msg = mock_ws.send_json.call_args[0][0]
        
        assert msg["event_type"] == "queue_shard_update"
        assert msg["data"]["shard_id"] == "priority"


class TestPublishHealthEvents:
    """Tests for health-related publish methods."""
    
    @pytest.fixture
    def manager(self):
        """Create manager."""
        return WebSocketConnectionManager()
    
    @pytest.fixture
    def publisher(self, manager):
        """Create publisher."""
        return WebSocketEventPublisher(manager)
    
    @pytest.fixture
    def mock_ws(self):
        """Create mock WebSocket."""
        ws = MagicMock()
        ws.accept = AsyncMock()
        ws.send_json = AsyncMock()
        ws.close = AsyncMock()
        return ws
    
    @pytest.mark.asyncio
    async def test_publish_health_update(self, publisher, manager, mock_ws):
        """Test publishing health_update event."""
        await manager.connect(mock_ws)
        
        health_data = {
            "status": "healthy",
            "postgres_ready": True,
            "redis_ready": True,
            "docker_ready": True
        }
        
        sent = await publisher.publish_health_event(EventType.HEALTH_UPDATE, health_data)
        
        assert sent == 1
        msg = mock_ws.send_json.call_args[0][0]
        
        assert msg["event_type"] == "health_update"
        assert msg["data"] == health_data
    
    @pytest.mark.asyncio
    async def test_publish_system_health(self, publisher, manager, mock_ws):
        """Test publishing system_health event."""
        await manager.connect(mock_ws)
        
        health_data = {
            "cpu_percent": 45.2,
            "memory_percent": 62.1,
            "disk_percent": 78.3
        }
        
        sent = await publisher.publish_health_event(EventType.SYSTEM_HEALTH, health_data)
        
        assert sent == 1
        msg = mock_ws.send_json.call_args[0][0]
        
        assert msg["event_type"] == "system_health"
        assert msg["data"]["cpu_percent"] == 45.2
    
    @pytest.mark.asyncio
    async def test_publish_health_degraded(self, publisher, manager, mock_ws):
        """Test publishing health with degraded status."""
        await manager.connect(mock_ws)
        
        health_data = {
            "status": "degraded",
            "postgres_ready": True,
            "redis_ready": False
        }
        
        sent = await publisher.publish_health_event(EventType.HEALTH_UPDATE, health_data)
        
        assert sent == 1
        msg = mock_ws.send_json.call_args[0][0]
        
        assert msg["data"]["status"] == "degraded"
        assert msg["data"]["redis_ready"] is False


class TestPublishRunEvents:
    """Tests for run-related publish methods."""
    
    @pytest.fixture
    def manager(self):
        """Create manager."""
        return WebSocketConnectionManager()
    
    @pytest.fixture
    def publisher(self, manager):
        """Create publisher."""
        return WebSocketEventPublisher(manager)
    
    @pytest.fixture
    def mock_ws(self):
        """Create mock WebSocket."""
        ws = MagicMock()
        ws.accept = AsyncMock()
        ws.send_json = AsyncMock()
        ws.close = AsyncMock()
        return ws
    
    @pytest.mark.asyncio
    async def test_publish_run_created(self, publisher, manager, mock_ws):
        """Test publishing run_created event."""
        await manager.connect(mock_ws)
        
        sent = await publisher.publish_run_event(
            EventType.RUN_CREATED,
            "r-001",
            task_id="t-001",
            worker_id="w-001"
        )
        
        assert sent == 1
        msg = mock_ws.send_json.call_args[0][0]
        
        assert msg["event_type"] == "run_created"
        assert msg["data"]["run_id"] == "r-001"
        assert msg["data"]["task_id"] == "t-001"
    
    @pytest.mark.asyncio
    async def test_publish_run_started(self, publisher, manager, mock_ws):
        """Test publishing run_started event."""
        await manager.connect(mock_ws)
        
        sent = await publisher.publish_run_event(
            EventType.RUN_STARTED,
            "r-001",
            worker_id="w-001"
        )
        
        assert sent == 1
        msg = mock_ws.send_json.call_args[0][0]
        
        assert msg["event_type"] == "run_started"
        assert msg["data"]["run_id"] == "r-001"
    
    @pytest.mark.asyncio
    async def test_publish_run_completed(self, publisher, manager, mock_ws):
        """Test publishing run_completed event."""
        await manager.connect(mock_ws)
        
        sent = await publisher.publish_run_event(
            EventType.RUN_COMPLETED,
            "r-001",
            success=True,
            duration_seconds=15.5
        )
        
        assert sent == 1
        msg = mock_ws.send_json.call_args[0][0]
        
        assert msg["event_type"] == "run_completed"
        assert msg["data"]["run_id"] == "r-001"
        assert msg["data"]["success"] is True
        assert msg["data"]["duration_seconds"] == 15.5
    
    @pytest.mark.asyncio
    async def test_publish_run_failed(self, publisher, manager, mock_ws):
        """Test publishing run_failed event."""
        await manager.connect(mock_ws)
        
        sent = await publisher.publish_run_event(
            EventType.RUN_FAILED,
            "r-001",
            error="OOM",
            traceback="..."
        )
        
        assert sent == 1
        msg = mock_ws.send_json.call_args[0][0]
        
        assert msg["event_type"] == "run_failed"
        assert msg["data"]["error"] == "OOM"


class TestPublishSystemEvents:
    """Tests for system event publishing."""
    
    @pytest.fixture
    def manager(self):
        """Create manager."""
        return WebSocketConnectionManager()
    
    @pytest.fixture
    def publisher(self, manager):
        """Create publisher."""
        return WebSocketEventPublisher(manager)
    
    @pytest.fixture
    def mock_ws(self):
        """Create mock WebSocket."""
        ws = MagicMock()
        ws.accept = AsyncMock()
        ws.send_json = AsyncMock()
        ws.close = AsyncMock()
        return ws
    
    @pytest.mark.asyncio
    async def test_publish_system_event_info(self, publisher, manager, mock_ws):
        """Test publishing system_event with info level."""
        await manager.connect(mock_ws)
        
        sent = await publisher.publish_system_event(
            event_name="config_reload",
            message="Configuration reloaded successfully",
            level="info",
            details={"version": "v2.1.0"}
        )
        
        assert sent == 1
        msg = mock_ws.send_json.call_args[0][0]
        
        assert msg["event_type"] == "system_event"
        assert msg["data"]["event_name"] == "config_reload"
        assert msg["data"]["message"] == "Configuration reloaded successfully"
        assert msg["data"]["level"] == "info"
        assert msg["data"]["details"]["version"] == "v2.1.0"
    
    @pytest.mark.asyncio
    async def test_publish_system_event_warning(self, publisher, manager, mock_ws):
        """Test publishing system_event with warning level."""
        await manager.connect(mock_ws)
        
        sent = await publisher.publish_system_event(
            event_name="worker_timeout",
            message="Worker w-001 heartbeat timeout",
            level="warning"
        )
        
        assert sent == 1
        msg = mock_ws.send_json.call_args[0][0]
        
        assert msg["data"]["level"] == "warning"
        assert msg["data"]["details"] == {}
    
    @pytest.mark.asyncio
    async def test_publish_system_event_error(self, publisher, manager, mock_ws):
        """Test publishing system_event with error level."""
        await manager.connect(mock_ws)
        
        sent = await publisher.publish_system_event(
            event_name="critical_failure",
            message="PostgreSQL connection lost",
            level="error",
            details={"retry_count": 5}
        )
        
        assert sent == 1
        msg = mock_ws.send_json.call_args[0][0]
        
        assert msg["data"]["level"] == "error"


class TestMultipleRecipients:
    """Tests for broadcasting to multiple clients."""
    
    @pytest.fixture
    def manager(self):
        """Create manager."""
        return WebSocketConnectionManager()
    
    @pytest.fixture
    def publisher(self, manager):
        """Create publisher."""
        return WebSocketEventPublisher(manager)
    
    @pytest.fixture
    def mock_ws_factory(self):
        """Factory for creating mock WebSocket."""
        def create():
            ws = MagicMock()
            ws.accept = AsyncMock()
            ws.send_json = AsyncMock()
            ws.close = AsyncMock()
            return ws
        return create
    
    @pytest.mark.asyncio
    async def test_broadcast_to_multiple_clients(self, publisher, manager, mock_ws_factory):
        """Test that multiple clients receive the same event."""
        clients = [mock_ws_factory() for _ in range(5)]
        
        for ws in clients:
            await manager.connect(ws)
        
        sent = await publisher.publish_worker_event(EventType.WORKER_JOINED, "w-001")
        
        assert sent == 5
        for ws in clients:
            ws.send_json.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_filtered_broadcast(self, publisher, manager, mock_ws_factory):
        """Test that only matching clients receive filtered events."""
        ws_all = mock_ws_factory()
        ws_worker = mock_ws_factory()
        
        await manager.connect(ws_all)
        await manager.connect(ws_worker)
        
        # Filter ws_worker to only worker_id w-001
        await manager.update_filters(ws_worker, {
            "action": "set",
            "worker_ids": ["w-001"]
        })
        
        # Broadcast for w-001
        sent1 = await publisher.publish_worker_event(EventType.WORKER_JOINED, "w-001")
        
        # Both should receive (ws_all has empty filters)
        assert sent1 == 2
        
        # Broadcast for w-002
        ws_all.send_json.reset_mock()
        ws_worker.send_json.reset_mock()
        
        sent2 = await publisher.publish_worker_event(EventType.WORKER_JOINED, "w-002")
        
        # Only ws_all should receive
        assert sent2 == 1
        ws_all.send_json.assert_called_once()
        ws_worker.send_json.assert_not_called()


class TestTimestampAndSequence:
    """Tests for timestamp and sequence generation."""
    
    @pytest.fixture
    def manager(self):
        """Create manager."""
        return WebSocketConnectionManager()
    
    @pytest.fixture
    def publisher(self, manager):
        """Create publisher."""
        return WebSocketEventPublisher(manager)
    
    @pytest.fixture
    def mock_ws(self):
        """Create mock WebSocket."""
        ws = MagicMock()
        ws.accept = AsyncMock()
        ws.send_json = AsyncMock()
        ws.close = AsyncMock()
        return ws
    
    @pytest.mark.asyncio
    async def test_timestamp_is_recent(self, publisher, manager, mock_ws):
        """Test that message timestamp is recent."""
        import time
        
        await manager.connect(mock_ws)
        
        before = time.time()
        await publisher.publish_worker_event(EventType.WORKER_JOINED, "w-001")
        after = time.time()
        
        msg = mock_ws.send_json.call_args[0][0]
        ts = msg["timestamp"] / 1000  # Convert from ms
        
        # Timestamp should be between before and after (with some tolerance)
        assert before - 1 <= ts <= after + 1
    
    @pytest.mark.asyncio
    async def test_sequence_increments(self, publisher, manager, mock_ws):
        """Test that sequence number increments."""
        await manager.connect(mock_ws)
        
        await publisher.publish_worker_event(EventType.WORKER_JOINED, "w-001")
        msg1 = mock_ws.send_json.call_args[0][0]
        seq1 = msg1["sequence"]
        
        mock_ws.send_json.reset_mock()
        
        await publisher.publish_worker_event(EventType.WORKER_LEFT, "w-001")
        msg2 = mock_ws.send_json.call_args[0][0]
        seq2 = msg2["sequence"]
        
        assert seq2 > seq1
    
    @pytest.mark.asyncio
    async def test_all_events_have_envelope_fields(self, publisher, manager, mock_ws):
        """Test that all events have envelope fields."""
        await manager.connect(mock_ws)
        
        # Test various event types
        event_types = [
            EventType.WORKER_JOINED,
            EventType.WORKER_LEFT,
            EventType.LEASE_CREATED,
            EventType.HEALTH_UPDATE,
            EventType.RUN_STARTED,
        ]
        
        for event_type in event_types:
            mock_ws.send_json.reset_mock()
            
            if event_type in [EventType.WORKER_JOINED, EventType.WORKER_LEFT]:
                await publisher.publish_worker_event(event_type, "w-001")
            elif event_type == EventType.LEASE_CREATED:
                await publisher.publish_lease_event(event_type, "l-001")
            elif event_type == EventType.HEALTH_UPDATE:
                await publisher.publish_health_event(event_type, {"status": "ok"})
            elif event_type == EventType.RUN_STARTED:
                await publisher.publish_run_event(event_type, "r-001")
            
            msg = mock_ws.send_json.call_args[0][0]
            
            assert "event_type" in msg
            assert "timestamp" in msg
            assert "sequence" in msg
            assert "data" in msg
            assert "filters" in msg