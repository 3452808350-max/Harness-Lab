"""
WebSocket Worker for Textual TUI.

Bridges WebSocket client to Textual's Worker and Message systems.
Features:
- Background WebSocket management via Textual Worker
- Message posting to App on events
- Connection state notifications
- Graceful shutdown
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional, Any, Dict

from textual.worker import Worker
from textual.message import Message

from .ws_client import (
    TUIWebSocketClient,
    WSConfig,
    ConnectionState,
    create_ws_client,
)


class WSMessage(Message):
    """WebSocket message posted to App.
    
    Attributes:
        data: Raw message data dict
        event_type: Extracted event type
        timestamp: Message timestamp
    """
    
    def __init__(self, data: dict):
        super().__init__()
        self.data = data
        self.event_type = data.get("event_type", "unknown")
        self.timestamp = data.get("timestamp", time.time())


class WSConnectionState(Message):
    """WebSocket connection state change message.
    
    Posted when connection state changes.
    
    Attributes:
        state: New ConnectionState
        error: Optional error message
        retry_count: Current retry attempt
    """
    
    def __init__(
        self,
        state: ConnectionState,
        error: str = None,
        retry_count: int = 0
    ):
        super().__init__()
        self.state = state
        self.error = error
        self.retry_count = retry_count


class WSHeartbeat(Message):
    """WebSocket heartbeat received.
    
    Posted periodically to indicate connection health.
    """
    
    def __init__(self, timestamp: float):
        super().__init__()
        self.timestamp = timestamp


class WebSocketWorker(Worker):
    """Textual Worker that manages WebSocket connection.
    
    This worker:
    1. Runs WebSocket client in background
    2. Parses incoming messages
    3. Posts appropriate Message to the App
    4. Handles connection state changes
    
    Usage in DashboardScreen:
        def on_mount(self):
            self._ws_worker = WebSocketWorker(
                WSConfig(url="ws://localhost:4600/ws")
            )
            self.run_worker(self._ws_worker)
        
        def on_ws_message(self, message: WSMessage):
            # Handle typed messages based on event_type
            if message.event_type == "worker_joined":
                self._handle_worker_joined(message.data)
    
    def on_ws_connection_state(self, message: WSConnectionState):
        # Handle connection state changes
        if message.state == ConnectionState.CONNECTED:
            self._ws_connected = True
        elif message.state == ConnectionState.ERROR:
            self._ws_connected = False
            # Start HTTP fallback
    
    def on_unmount(self):
        self._ws_worker.stop_worker()
    """
    
    def __init__(
        self,
        config: WSConfig = None,
        use_mock: bool = False,
        **kwargs
    ):
        """Initialize WebSocket Worker.
        
        Args:
            config: WebSocket configuration
            use_mock: Use mock client for testing
        """
        super().__init__(**kwargs)
        self._config = config or WSConfig()
        self._use_mock = use_mock
        self._client: Optional[TUIWebSocketClient] = None
        self._running = False
    
    async def run(self) -> None:
        """Worker main loop - manages WebSocket connection."""
        self._running = True
        
        # Create client (real or mock)
        self._client = create_ws_client(self._config, use_mock=self._use_mock)
        
        # Set handlers that post messages to App
        self._client.set_message_handler(self._handle_message)
        self._client.set_connection_handler(self._handle_connection)
        
        try:
            await self._client.start()
            
            # Keep worker alive while client running
            while self._running and self._client.is_connected:
                await asyncio.sleep(1.0)
                
        except asyncio.CancelledError:
            # Worker was cancelled - stop client
            pass
            
        finally:
            await self._client.stop()
    
    async def _handle_message(self, data: Dict) -> None:
        """Process WebSocket message and post to App."""
        # Post typed message to App
        self.app.post_message(WSMessage(data))
        
        # Special handling for heartbeat
        if data.get("event_type") == "heartbeat":
            self.app.post_message(WSHeartbeat(data.get("timestamp", time.time())))
    
    async def _handle_connection(self, state: ConnectionState) -> None:
        """Handle connection state changes and post to App."""
        error = self._client.last_error if self._client else None
        retry_count = self._client.retry_count if self._client else 0
        
        self.app.post_message(WSConnectionState(state, error, retry_count))
    
    def stop_worker(self) -> None:
        """Stop the worker gracefully."""
        self._running = False
        
        if self._client:
            # Schedule stop in worker's async context
            asyncio.create_task(self._client.stop())
        
        # Cancel worker
        self.cancel()
    
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._client and self._client.is_connected
    
    def get_state(self) -> ConnectionState:
        """Get current connection state."""
        return self._client.state if self._client else ConnectionState.DISCONNECTED
    
    async def send(self, data: Dict) -> bool:
        """Send message via WebSocket.
        
        Args:
            data: Message data to send
            
        Returns:
            True if sent successfully
        """
        if not self._client:
            return False
        return await self._client.send(data)


# === Typed Message Classes for Specific Events ===
# These can be used instead of generic WSMessage for cleaner handling

class WSWorkerJoined(Message):
    """Worker joined the fleet."""
    
    def __init__(self, worker_id: str, role: str, state: str = "idle"):
        super().__init__()
        self.worker_id = worker_id
        self.role = role
        self.state = state


class WSWorkerLeft(Message):
    """Worker left the fleet."""
    
    def __init__(self, worker_id: str):
        super().__init__()
        self.worker_id = worker_id


class WSWorkerStateChanged(Message):
    """Worker state changed."""
    
    def __init__(self, worker_id: str, old_state: str, new_state: str, task_id: str = None):
        super().__init__()
        self.worker_id = worker_id
        self.old_state = old_state
        self.new_state = new_state
        self.task_id = task_id


class WSTaskDispatched(Message):
    """Task dispatched to worker."""
    
    def __init__(self, task_id: str, worker_id: str):
        super().__init__()
        self.task_id = task_id
        self.worker_id = worker_id


class WSTaskCompleted(Message):
    """Task completed."""
    
    def __init__(self, task_id: str, worker_id: str, success: bool):
        super().__init__()
        self.task_id = task_id
        self.worker_id = worker_id
        self.success = success


class WSHealthUpdate(Message):
    """Health status update."""
    
    def __init__(self, health_data: dict):
        super().__init__()
        self.health_data = health_data


class WSQueueUpdate(Message):
    """Queue status update."""
    
    def __init__(self, queues_data: list):
        super().__init__()
        self.queues_data = queues_data


class WSInitialState(Message):
    """Initial state snapshot."""
    
    def __init__(self, health: dict, workers: list, queues: list):
        super().__init__()
        self.health = health
        self.workers = workers
        self.queues = queues


class TypedWebSocketWorker(WebSocketWorker):
    """WebSocket Worker that posts typed messages.
    
    Instead of posting generic WSMessage, this posts specific message types
    like WSWorkerJoined, WSTaskDispatched, etc.
    
    This allows widgets to use cleaner handler methods:
        def on_ws_worker_joined(self, message: WSWorkerJoined):
            self.add_worker(message.worker_id, message.state, message.role)
    """
    
    async def _handle_message(self, data: Dict) -> None:
        """Process WebSocket message and post typed message."""
        event_type = data.get("event_type", "unknown")
        event_data = data.get("data", {})
        
        # Map event types to message classes
        if event_type == "initial_state":
            state_data = event_data
            self.app.post_message(
                WSInitialState(
                    health=state_data.get("health", {}),
                    workers=state_data.get("workers", []),
                    queues=state_data.get("queues", []),
                )
            )
        
        elif event_type == "worker_joined":
            self.app.post_message(
                WSWorkerJoined(
                    worker_id=event_data.get("worker_id"),
                    role=event_data.get("role", "general"),
                    state=event_data.get("state", "idle"),
                )
            )
        
        elif event_type == "worker_left":
            self.app.post_message(
                WSWorkerLeft(worker_id=event_data.get("worker_id"))
            )
        
        elif event_type == "worker_state_changed":
            self.app.post_message(
                WSWorkerStateChanged(
                    worker_id=event_data.get("worker_id"),
                    old_state=event_data.get("old_state"),
                    new_state=event_data.get("new_state"),
                    task_id=event_data.get("task_id"),
                )
            )
        
        elif event_type == "task_dispatched":
            self.app.post_message(
                WSTaskDispatched(
                    task_id=event_data.get("task_id"),
                    worker_id=event_data.get("worker_id"),
                )
            )
        
        elif event_type == "task_completed":
            self.app.post_message(
                WSTaskCompleted(
                    task_id=event_data.get("task_id"),
                    worker_id=event_data.get("worker_id"),
                    success=event_data.get("success", True),
                )
            )
        
        elif event_type == "health_update":
            self.app.post_message(WSHealthUpdate(event_data))
        
        elif event_type == "queue_update":
            self.app.post_message(WSQueueUpdate(event_data))
        
        elif event_type == "heartbeat":
            self.app.post_message(WSHeartbeat(data.get("timestamp", time.time())))
        
        else:
            # Unknown event type - post generic message
            self.app.post_message(WSMessage(data))