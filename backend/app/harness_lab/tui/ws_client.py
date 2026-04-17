"""
Harness Lab TUI WebSocket Client.

Provides real-time WebSocket connection for Control Plane events.
Features:
- Automatic connection management
- Exponential backoff reconnection
- Message parsing and dispatch
- Connection state tracking
- Graceful degradation to HTTP polling
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Callable, Awaitable, List
from enum import Enum
from datetime import datetime

try:
    import websockets
    from websockets.client import WebSocketClientProtocol
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    WebSocketClientProtocol = None


class ConnectionState(Enum):
    """WebSocket connection state."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"


@dataclass
class WSConfig:
    """Configuration for WebSocket client.
    
    Attributes:
        url: WebSocket URL (e.g., ws://localhost:4600/ws)
        reconnect_delay: Base reconnect delay (seconds)
        max_reconnect_delay: Maximum reconnect delay (seconds)
        ping_interval: WebSocket ping interval (seconds)
        ping_timeout: WebSocket ping timeout (seconds)
        max_retries: Maximum reconnect attempts before fallback
        message_timeout: Timeout for individual message processing
        heartbeat_interval: Interval for connection health checks
    """
    url: str = "ws://localhost:4600/ws"
    reconnect_delay: float = 1.0
    max_reconnect_delay: float = 30.0
    ping_interval: float = 20.0
    ping_timeout: float = 20.0
    max_retries: int = 10
    message_timeout: float = 5.0
    heartbeat_interval: float = 30.0


class TUIWebSocketClient:
    """Async WebSocket client for real-time Control Plane events.
    
    Features:
    - Automatic connection management
    - Exponential backoff reconnection
    - Message parsing and dispatch
    - Connection state tracking
    - Graceful degradation support
    
    Usage:
        client = TUIWebSocketClient(WSConfig(url="ws://localhost:4600/ws"))
        client.set_message_handler(handle_event)
        await client.start()
        
        # ... later ...
        await client.stop()
    
    Attributes:
        state: Current ConnectionState
        last_error: Last error encountered
        retry_count: Current retry attempt count
        is_connected: Whether WebSocket is connected
    """
    
    def __init__(self, config: WSConfig = None):
        """Initialize WebSocket client.
        
        Args:
            config: WebSocket configuration (uses defaults if not provided)
        """
        self._config = config or WSConfig()
        self._ws: Optional[WebSocketClientProtocol] = None
        self._state: ConnectionState = ConnectionState.DISCONNECTED
        self._last_error: Optional[str] = None
        self._retry_count: int = 0
        self._running: bool = False
        self._last_message_time: float = 0
        
        # Handlers
        self._message_handlers: List[Callable[[Dict], Awaitable[None]]] = []
        self._connection_handlers: List[Callable[[ConnectionState], Awaitable[None]]] = []
        
        # Internal state
        self._connection_task: Optional[asyncio.Task] = None
        self._listen_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        
        # Heartbeat tracking
        self._last_ping_time: float = 0
        self._last_pong_time: float = 0
        
    @property
    def state(self) -> ConnectionState:
        """Get current connection state."""
        return self._state
    
    @property
    def last_error(self) -> Optional[str]:
        """Get last error message."""
        return self._last_error
    
    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._state == ConnectionState.CONNECTED
    
    @property
    def config(self) -> WSConfig:
        """Get current configuration."""
        return self._config
    
    def set_message_handler(self, handler: Callable[[Dict], Awaitable[None]]) -> None:
        """Add callback for received messages.
        
        Args:
            handler: Async function to process received messages
        """
        self._message_handlers.append(handler)
    
    def set_connection_handler(self, handler: Callable[[ConnectionState], Awaitable[None]]) -> None:
        """Add callback for connection state changes.
        
        Args:
            handler: Async function called on state changes
        """
        self._connection_handlers.append(handler)
    
    def clear_handlers(self) -> None:
        """Clear all handlers."""
        self._message_handlers.clear()
        self._connection_handlers.clear()
    
    async def _connection_loop(self) -> None:
        """Main connection loop with reconnect logic."""
        while self._running:
            try:
                await self._connect()
                
                # Listen for messages
                await self._listen()
                
            except asyncio.CancelledError:
                break
                
            except Exception as e:
                self._last_error = str(e)
                self._state = ConnectionState.ERROR
                
                if not self._running:
                    break
                
                # Attempt reconnect
                await self._reconnect()
                
                if self._state == ConnectionState.ERROR:
                    # Max retries exhausted
                    break
    
    async def _connect(self) -> None:
        """Establish WebSocket connection."""
        self._state = ConnectionState.CONNECTING
        await self._notify_connection_handlers(ConnectionState.CONNECTING)
        
        try:
            self._ws = await websockets.connect(
                self._config.url,
                ping_interval=self._config.ping_interval,
                ping_timeout=self._config.ping_timeout,
                close_timeout=5.0,
            )
            
            self._state = ConnectionState.CONNECTED
            self._retry_count = 0
            self._last_error = None
            self._last_message_time = time.time()
            
            await self._notify_connection_handlers(ConnectionState.CONNECTED)
            
        except Exception as e:
            self._last_error = f"Connection failed: {e}"
            raise
    
    async def _listen(self) -> None:
        """Listen for WebSocket messages."""
        if not self._ws:
            raise RuntimeError("WebSocket not connected")
        
        # Start heartbeat loop after connection established
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        
        try:
            async for message in self._ws:
                if not self._running:
                    break
                
                self._last_message_time = time.time()
                
                try:
                    data = json.loads(message)
                    
                    # Handle pong responses
                    if data.get("event_type") == "pong":
                        self._last_pong_time = time.time()
                        # Don't dispatch pong to handlers, just update time
                        continue
                    
                    await self._dispatch_message(data)
                    
                except json.JSONDecodeError as e:
                    self._last_error = f"JSON decode error: {e}"
                    # Continue listening despite parse errors
                    
                except Exception as e:
                    self._last_error = f"Message handling error: {e}"
                    # Continue listening
                    
        except websockets.ConnectionClosed as e:
            self._last_error = f"Connection closed: {e.code} {e.reason}"
            raise
            
        except Exception as e:
            self._last_error = f"Listen error: {e}"
            raise
    
    async def _dispatch_message(self, data: Dict) -> None:
        """Dispatch message to handlers with timeout."""
        for handler in self._message_handlers:
            try:
                await asyncio.wait_for(
                    handler(data),
                    timeout=self._config.message_timeout
                )
            except asyncio.TimeoutError:
                self._last_error = "Message handler timeout"
                # Don't propagate - just log
            except Exception as e:
                self._last_error = f"Handler error: {e}"
                # Don't propagate - just log
    
    async def _reconnect(self) -> None:
        """Handle reconnection with exponential backoff."""
        self._retry_count += 1
        self._state = ConnectionState.RECONNECTING
        await self._notify_connection_handlers(ConnectionState.RECONNECTING)
        
        # Check max retries
        if self._retry_count > self._config.max_retries:
            self._state = ConnectionState.ERROR
            self._last_error = f"Max retries ({self._config.max_retries}) exhausted"
            await self._notify_connection_handlers(ConnectionState.ERROR)
            return
        
        # Close old connection
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        
        # Exponential backoff with cap
        delay = min(
            self._config.reconnect_delay * (2 ** (self._retry_count - 1)),
            self._config.max_reconnect_delay
        )
        
        # Wait before reconnect
        await asyncio.sleep(delay)
    
    async def _notify_connection_handlers(self, state: ConnectionState) -> None:
        """Notify connection handlers of state change."""
        for handler in self._connection_handlers:
            try:
                await handler(state)
            except Exception:
                pass  # Don't propagate handler errors
    
    async def send(self, data: Dict) -> bool:
        """Send message to WebSocket.
        
        Args:
            data: Message data to send (will be JSON encoded)
            
        Returns:
            True if sent successfully, False otherwise
        """
        if not self._ws or not self.is_connected:
            return False
        
        try:
            await self._ws.send(json.dumps(data))
            return True
        except Exception as e:
            self._last_error = f"Send error: {e}"
            return False
    
    def get_connection_age(self) -> float:
        """Get seconds since last message received.
        
        Useful for heartbeat detection.
        """
        if self._last_message_time == 0:
            return 0
        return time.time() - self._last_message_time
    
    def is_healthy(self) -> bool:
        """Check if connection is healthy (recent messages)."""
        if not self.is_connected:
            return False
        age = self.get_connection_age()
        # Consider unhealthy if no messages for 2x heartbeat interval
        return age < self._config.heartbeat_interval * 2
    
    async def send_ping(self) -> bool:
        """Send ping message to server for heartbeat.
        
        Returns:
            True if ping sent successfully
        """
        return await self.send({"event_type": "ping", "timestamp": time.time()})
    
    async def _heartbeat_loop(self) -> None:
        """Heartbeat loop for sending periodic pings."""
        while self._running and self.is_connected:
            try:
                # Send ping
                await self.send_ping()
                
                # Wait for next heartbeat interval
                await asyncio.sleep(self._config.heartbeat_interval)
                
            except asyncio.CancelledError:
                break
            except Exception:
                # Ping failed - connection may be broken
                break
    
    async def start(self) -> None:
        """Start WebSocket connection loop.
        
        This creates a background task that manages the connection.
        """
        if self._running:
            return
        
        if not WEBSOCKETS_AVAILABLE:
            self._last_error = "websockets package not installed"
            self._state = ConnectionState.ERROR
            await self._notify_connection_handlers(ConnectionState.ERROR)
            return
        
        self._running = True
        self._retry_count = 0
        
        # Start connection loop as background task
        self._connection_task = asyncio.create_task(self._connection_loop())
    
    async def stop(self) -> None:
        """Stop WebSocket connection."""
        self._running = False
        
        # Cancel heartbeat task
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None
        
        # Cancel tasks
        if self._connection_task:
            self._connection_task.cancel()
            try:
                await self._connection_task
            except asyncio.CancelledError:
                pass
            self._connection_task = None
        
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None
        
        # Close WebSocket
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        
        self._state = ConnectionState.DISCONNECTED
        await self._notify_connection_handlers(ConnectionState.DISCONNECTED)


class MockWebSocketClient(TUIWebSocketClient):
    """Mock WebSocket client for testing/demo when websockets unavailable.
    
    Simulates connection and generates periodic fake events.
    """
    
    def __init__(self, config: WSConfig = None):
        # Skip parent's websockets check
        self._config = config or WSConfig()
        self._state: ConnectionState = ConnectionState.DISCONNECTED
        self._last_error: Optional[str] = None
        self._retry_count: int = 0
        self._running: bool = False
        self._message_handlers: List[Callable[[Dict], Awaitable[None]]] = []
        self._connection_handlers: List[Callable[[ConnectionState], Awaitable[None]]] = []
        self._demo_task: Optional[asyncio.Task] = None
    
    async def start(self) -> None:
        """Start mock connection."""
        if self._running:
            return
        
        self._running = True
        self._state = ConnectionState.CONNECTING
        await self._notify_connection_handlers(ConnectionState.CONNECTING)
        
        # Simulate connection delay
        await asyncio.sleep(0.5)
        
        self._state = ConnectionState.CONNECTED
        self._retry_count = 0
        await self._notify_connection_handlers(ConnectionState.CONNECTED)
        
        # Send mock initial state
        await self._dispatch_message({
            "event_type": "initial_state",
            "timestamp": time.time(),
            "data": {
                "health": {
                    "status": "healthy",
                    "postgres_ready": True,
                    "redis_ready": True,
                    "docker_ready": True,
                },
                "workers": [
                    {"worker_id": "w-demo-01", "state": "idle", "role_profile": "general"},
                    {"worker_id": "w-demo-02", "state": "executing", "role_profile": "executor"},
                ],
                "queues": [
                    {"shard": "default", "depth": 5, "sample_tasks": ["T-01", "T-02"]},
                ],
            }
        })
        
        # Start demo event loop
        self._demo_task = asyncio.create_task(self._demo_event_loop())
    
    async def stop(self) -> None:
        """Stop mock connection."""
        self._running = False
        
        if self._demo_task:
            self._demo_task.cancel()
            try:
                await self._demo_task
            except asyncio.CancelledError:
                pass
        
        self._state = ConnectionState.DISCONNECTED
        await self._notify_connection_handlers(ConnectionState.DISCONNECTED)
    
    async def _demo_event_loop(self) -> None:
        """Generate periodic demo events."""
        import random
        
        workers = ["w-demo-01", "w-demo-02", "w-demo-03"]
        event_types = ["worker_joined", "worker_state_changed", "task_dispatched", "heartbeat"]
        
        while self._running:
            await asyncio.sleep(random.uniform(5.0, 15.0))
            
            if not self._running:
                break
            
            event_type = random.choice(event_types)
            
            if event_type == "heartbeat":
                await self._dispatch_message({
                    "event_type": "heartbeat",
                    "timestamp": time.time(),
                })
            elif event_type == "worker_joined":
                worker_id = f"w-demo-{random.randint(10, 99)}"
                await self._dispatch_message({
                    "event_type": "worker_joined",
                    "timestamp": time.time(),
                    "data": {
                        "worker_id": worker_id,
                        "role": "general",
                        "state": "idle",
                    }
                })
            elif event_type == "worker_state_changed":
                worker_id = random.choice(workers)
                states = ["idle", "executing", "draining"]
                await self._dispatch_message({
                    "event_type": "worker_state_changed",
                    "timestamp": time.time(),
                    "data": {
                        "worker_id": worker_id,
                        "old_state": random.choice(states),
                        "new_state": random.choice(states),
                    }
                })
            elif event_type == "task_dispatched":
                await self._dispatch_message({
                    "event_type": "task_dispatched",
                    "timestamp": time.time(),
                    "data": {
                        "task_id": f"T-{random.randint(100, 999)}",
                        "worker_id": random.choice(workers),
                    }
                })
    
    async def send(self, data: Dict) -> bool:
        """Mock send - always succeeds."""
        return True


def create_ws_client(config: WSConfig = None, use_mock: bool = False) -> TUIWebSocketClient:
    """Create WebSocket client, using mock if websockets unavailable.
    
    Args:
        config: WebSocket configuration
        use_mock: Force use of mock client
        
    Returns:
        TUIWebSocketClient or MockWebSocketClient
    """
    if use_mock or not WEBSOCKETS_AVAILABLE:
        return MockWebSocketClient(config)
    return TUIWebSocketClient(config)