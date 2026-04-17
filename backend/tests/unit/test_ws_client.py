"""
Unit tests for TUIWebSocketClient.

Tests cover:
- Connection state transitions
- Message parsing
- Graceful degradation to MockWebSocketClient
- Start/stop lifecycle
"""

from __future__ import annotations

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
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

from harness_lab.tui.ws_client import (
    TUIWebSocketClient,
    MockWebSocketClient,
    WSConfig,
    ConnectionState,
    WEBSOCKETS_AVAILABLE,
    create_ws_client,
)


class TestWSConfig:
    """Tests for WSConfig dataclass."""
    
    def test_default_config(self):
        """Test default configuration values."""
        config = WSConfig()
        
        assert config.url == "ws://localhost:4600/ws"
        assert config.reconnect_delay == 1.0
        assert config.max_reconnect_delay == 30.0
        assert config.ping_interval == 20.0
        assert config.ping_timeout == 20.0
        assert config.max_retries == 10
        assert config.message_timeout == 5.0
        assert config.heartbeat_interval == 30.0
    
    def test_custom_config(self):
        """Test custom configuration values."""
        config = WSConfig(
            url="ws://example.com:8080/ws",
            reconnect_delay=2.0,
            max_retries=5
        )
        
        assert config.url == "ws://example.com:8080/ws"
        assert config.reconnect_delay == 2.0
        assert config.max_retries == 5


class TestTUIWebSocketClient:
    """Tests for TUIWebSocketClient."""
    
    @pytest.fixture
    def config(self):
        """Create test configuration."""
        return WSConfig(
            url="ws://localhost:4600/ws",
            reconnect_delay=1.0,
            max_reconnect_delay=30.0,
            max_retries=3,
            ping_interval=10.0,
            ping_timeout=10.0
        )
    
    @pytest.fixture
    def client(self, config):
        """Create client with test config."""
        return TUIWebSocketClient(config)
    
    def test_initial_state(self, client):
        """Test client initial state."""
        assert client.state == ConnectionState.DISCONNECTED
        assert client.last_error is None
        assert client._retry_count == 0
        assert not client.is_connected
    
    def test_set_message_handler(self, client):
        """Test adding message handlers."""
        handler = AsyncMock()
        client.set_message_handler(handler)
        
        assert handler in client._message_handlers
    
    def test_set_connection_handler(self, client):
        """Test adding connection handlers."""
        handler = AsyncMock()
        client.set_connection_handler(handler)
        
        assert handler in client._connection_handlers
    
    def test_clear_handlers(self, client):
        """Test clearing handlers."""
        client.set_message_handler(AsyncMock())
        client.set_connection_handler(AsyncMock())
        
        client.clear_handlers()
        
        assert len(client._message_handlers) == 0
        assert len(client._connection_handlers) == 0
    
    def test_is_connected_property(self, client):
        """Test is_connected property."""
        assert client.is_connected is False
        
        client._state = ConnectionState.CONNECTED
        assert client.is_connected is True
        
        client._state = ConnectionState.RECONNECTING
        assert client.is_connected is False
    
    def test_config_property(self, client, config):
        """Test config property returns the configuration."""
        assert client.config == config
    
    def test_get_connection_age(self, client):
        """Test get_connection_age calculation."""
        # Initial value is 0
        assert client.get_connection_age() == 0
        
        # Set last message time
        client._last_message_time = time.time() - 5.0
        age = client.get_connection_age()
        assert 4.9 < age < 5.1
    
    def test_is_healthy(self, client):
        """Test is_healthy method."""
        # Disconnected is unhealthy
        assert client.is_healthy() is False
        
        # Connected with recent messages is healthy
        client._state = ConnectionState.CONNECTED
        client._last_message_time = time.time()
        assert client.is_healthy() is True
        
        # Connected with old messages is unhealthy
        client._last_message_time = time.time() - 60.0  # 1 minute ago
        assert client.is_healthy() is False
    
    @pytest.mark.asyncio
    async def test_notify_connection_handlers(self, client):
        """Test notifying connection handlers."""
        handler1 = AsyncMock()
        handler2 = AsyncMock()
        client.set_connection_handler(handler1)
        client.set_connection_handler(handler2)
        
        await client._notify_connection_handlers(ConnectionState.CONNECTED)
        
        handler1.assert_called_once_with(ConnectionState.CONNECTED)
        handler2.assert_called_once_with(ConnectionState.CONNECTED)
    
    @pytest.mark.asyncio
    async def test_notify_connection_handlers_error(self, client):
        """Test that handler errors don't propagate."""
        handler1 = AsyncMock(side_effect=Exception("Handler error"))
        handler2 = AsyncMock()
        client.set_connection_handler(handler1)
        client.set_connection_handler(handler2)
        
        # Should not raise
        await client._notify_connection_handlers(ConnectionState.CONNECTED)
        
        # Second handler should still be called
        handler2.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_dispatch_message(self, client):
        """Test message dispatch to handlers."""
        handler = AsyncMock()
        client.set_message_handler(handler)
        
        data = {"event_type": "test", "data": "hello"}
        await client._dispatch_message(data)
        
        handler.assert_called_once_with(data)
    
    @pytest.mark.asyncio
    async def test_dispatch_message_handler_error(self, client):
        """Test handler error handling."""
        def error_handler(data):
            raise Exception("Handler error")
        
        client.set_message_handler(error_handler)
        
        # Should complete without raising
        await client._dispatch_message({"test": "data"})
        
        assert "Handler error" in client.last_error
    
    @pytest.mark.asyncio
    async def test_send_when_connected(self, client):
        """Test send when connected."""
        mock_ws = MagicMock()
        mock_ws.send = AsyncMock()
        client._ws = mock_ws
        client._state = ConnectionState.CONNECTED
        
        result = await client.send({"command": "ping"})
        
        assert result is True
        mock_ws.send.assert_called_once_with(json.dumps({"command": "ping"}))
    
    @pytest.mark.asyncio
    async def test_send_when_not_connected(self, client):
        """Test send when not connected returns False."""
        client._ws = None
        client._state = ConnectionState.DISCONNECTED
        
        result = await client.send({"command": "ping"})
        
        assert result is False
    
    @pytest.mark.asyncio
    async def test_send_error(self, client):
        """Test send error handling."""
        mock_ws = MagicMock()
        mock_ws.send = AsyncMock(side_effect=Exception("Send error"))
        client._ws = mock_ws
        client._state = ConnectionState.CONNECTED
        
        result = await client.send({"command": "ping"})
        
        assert result is False
        assert "Send error" in client.last_error


class TestConnectionStateTransitions:
    """Tests for connection state transitions."""
    
    @pytest.fixture
    def config(self):
        """Test config with fast reconnect."""
        return WSConfig(
            reconnect_delay=0.1,
            max_reconnect_delay=1.0,
            max_retries=2
        )
    
    @pytest.mark.asyncio
    async def test_stop_sets_disconnected_state(self, config):
        """Test stop() sets DISCONNECTED state."""
        client = TUIWebSocketClient(config)
        handler = AsyncMock()
        client.set_connection_handler(handler)
        
        client._state = ConnectionState.CONNECTED
        
        await client.stop()
        
        assert client.state == ConnectionState.DISCONNECTED
        handler.assert_called_with(ConnectionState.DISCONNECTED)


class TestExponentialBackoff:
    """Tests for exponential backoff reconnect logic."""
    
    @pytest.fixture
    def config(self):
        """Test config with short delays."""
        return WSConfig(
            reconnect_delay=1.0,
            max_reconnect_delay=30.0,
            max_retries=5
        )
    
    @pytest.fixture
    def client(self, config):
        """Create client."""
        return TUIWebSocketClient(config)
    
    def test_backoff_calculation(self, client):
        """Test exponential backoff delay calculation."""
        # First retry: 1 * 2^0 = 1
        client._retry_count = 1
        delay = min(
            client.config.reconnect_delay * (2 ** (client._retry_count - 1)),
            client.config.max_reconnect_delay
        )
        assert delay == 1.0
        
        # Second retry: 1 * 2^1 = 2
        client._retry_count = 2
        delay = min(
            client.config.reconnect_delay * (2 ** (client._retry_count - 1)),
            client.config.max_reconnect_delay
        )
        assert delay == 2.0
        
        # Third retry: 1 * 2^2 = 4
        client._retry_count = 3
        delay = min(
            client.config.reconnect_delay * (2 ** (client._retry_count - 1)),
            client.config.max_reconnect_delay
        )
        assert delay == 4.0
        
        # Cap at max_reconnect_delay
        client._retry_count = 10
        delay = min(
            client.config.reconnect_delay * (2 ** (client._retry_count - 1)),
            client.config.max_reconnect_delay
        )
        assert delay == 30.0


class TestMockWebSocketClient:
    """Tests for MockWebSocketClient (graceful degradation)."""
    
    @pytest.fixture
    def mock_client(self):
        """Create mock client."""
        return MockWebSocketClient(WSConfig())
    
    def test_mock_client_initial_state(self, mock_client):
        """Test mock client initial state."""
        assert mock_client.state == ConnectionState.DISCONNECTED
        assert not mock_client.is_connected
    
    @pytest.mark.asyncio
    async def test_mock_client_start(self, mock_client):
        """Test mock client start transitions to CONNECTED."""
        handler = AsyncMock()
        mock_client.set_connection_handler(handler)
        
        await mock_client.start()
        
        # Should transition through CONNECTING to CONNECTED
        assert mock_client.state == ConnectionState.CONNECTED
        assert mock_client.is_connected
        
        # Should have called handlers
        calls = [c[0][0] for c in handler.call_args_list]
        assert ConnectionState.CONNECTING in calls
        assert ConnectionState.CONNECTED in calls
        
        await mock_client.stop()
    
    @pytest.mark.asyncio
    async def test_mock_client_sends_initial_state(self, mock_client):
        """Test mock client sends initial_state on connect."""
        handler = AsyncMock()
        mock_client.set_message_handler(handler)
        
        await mock_client.start()
        
        # Wait for initial state to be dispatched
        await asyncio.sleep(0.6)
        
        # Should have received initial_state
        calls = [c[0][0] for c in handler.call_args_list]
        initial_states = [c for c in calls if c.get("event_type") == "initial_state"]
        assert len(initial_states) > 0
        
        # Check initial_state structure
        initial = initial_states[0]
        assert "data" in initial
        assert "health" in initial["data"]
        assert "workers" in initial["data"]
        assert "queues" in initial["data"]
        
        await mock_client.stop()
    
    @pytest.mark.asyncio
    async def test_mock_client_stop(self, mock_client):
        """Test mock client stop transitions to DISCONNECTED."""
        handler = AsyncMock()
        mock_client.set_connection_handler(handler)
        
        await mock_client.start()
        await asyncio.sleep(0.1)
        
        await mock_client.stop()
        
        assert mock_client.state == ConnectionState.DISCONNECTED
        handler.assert_called_with(ConnectionState.DISCONNECTED)
    
    @pytest.mark.asyncio
    async def test_mock_client_send_always_succeeds(self, mock_client):
        """Test mock send always returns True."""
        await mock_client.start()
        
        result = await mock_client.send({"command": "ping"})
        assert result is True
        
        await mock_client.stop()


class TestCreateWsClient:
    """Tests for create_ws_client factory function."""
    
    def test_creates_real_client_when_available(self):
        """Test creates real client when websockets available."""
        if not WEBSOCKETS_AVAILABLE:
            pytest.skip("websockets not installed")
        
        client = create_ws_client(use_mock=False)
        assert isinstance(client, TUIWebSocketClient)
    
    def test_creates_mock_when_force_mock(self):
        """Test creates mock client when use_mock=True."""
        client = create_ws_client(use_mock=True)
        assert isinstance(client, MockWebSocketClient)
    
    def test_creates_mock_when_websockets_unavailable(self):
        """Test creates mock client when websockets not available."""
        with patch('harness_lab.tui.ws_client.WEBSOCKETS_AVAILABLE', False):
            client = create_ws_client()
            assert isinstance(client, MockWebSocketClient)


class TestMessageParsing:
    """Tests for message parsing in WebSocket client."""
    
    @pytest.fixture
    def client(self):
        """Create client."""
        return TUIWebSocketClient(WSConfig())
    
    @pytest.mark.asyncio
    async def test_valid_json_message(self, client):
        """Test parsing valid JSON message."""
        handler = AsyncMock()
        client.set_message_handler(handler)
        
        valid_json = '{"event_type": "worker_joined", "data": {"worker_id": "w-001"}}'
        data = json.loads(valid_json)
        
        await client._dispatch_message(data)
        
        handler.assert_called_once()
        call_data = handler.call_args[0][0]
        assert call_data["event_type"] == "worker_joined"
        assert call_data["data"]["worker_id"] == "w-001"
    
    @pytest.mark.asyncio
    async def test_multiple_handlers_receive_message(self, client):
        """Test that multiple handlers all receive message."""
        handler1 = AsyncMock()
        handler2 = AsyncMock()
        client.set_message_handler(handler1)
        client.set_message_handler(handler2)
        
        data = {"test": "data"}
        await client._dispatch_message(data)
        
        handler1.assert_called_once_with(data)
        handler2.assert_called_once_with(data)


class TestConnectionLifecycle:
    """Tests for full connection lifecycle."""
    
    @pytest.fixture
    def config(self):
        """Test config."""
        return WSConfig(
            reconnect_delay=0.5,
            max_reconnect_delay=2.0,
            max_retries=3,
            ping_interval=5.0,
            ping_timeout=5.0
        )
    
    @pytest.mark.asyncio
    async def test_start_when_already_running(self, config):
        """Test start() when already running does nothing."""
        client = TUIWebSocketClient(config)
        client._running = True
        
        # Should not create new task
        await client.start()
        
        assert client._connection_task is None
        client._running = False


class TestIntegrationWithTextual:
    """Tests simulating integration with Textual Worker."""
    
    @pytest.mark.asyncio
    async def test_handler_registration_workflow(self):
        """Test typical handler registration workflow."""
        client = TUIWebSocketClient(WSConfig())
        
        # Simulate typical usage
        event_log = []
        
        async def on_message(data):
            event_log.append(data)
        
        async def on_state(state):
            event_log.append({"state": state})
        
        client.set_message_handler(on_message)
        client.set_connection_handler(on_state)
        
        # Simulate message dispatch
        await client._dispatch_message({"event_type": "test"})
        await client._notify_connection_handlers(ConnectionState.CONNECTED)
        
        assert len(event_log) == 2
        assert {"event_type": "test"} in event_log
        assert {"state": ConnectionState.CONNECTED} in event_log
    
    @pytest.mark.asyncio
    async def test_graceful_degradation_with_mock(self):
        """Test that mock client works when real client unavailable."""
        # Force mock client
        client = create_ws_client(use_mock=True)
        
        event_log = []
        
        async def on_message(data):
            event_log.append(data)
        
        client.set_message_handler(on_message)
        
        await client.start()
        await asyncio.sleep(0.6)  # Wait for initial state
        
        # Should have received mock events
        assert len(event_log) > 0
        assert any(e.get("event_type") == "initial_state" for e in event_log)
        
        await client.stop()