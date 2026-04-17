# Harness Lab TUI WebSocket Integration Design

## Overview

This document outlines the WebSocket integration strategy for the Harness Lab TUI, enabling real-time updates without HTTP polling.

### Current State

- **API Client**: `api_client.py` uses HTTP polling every 2 seconds
- **Backend**: FastAPI with no WebSocket endpoints
- **TUI Dashboard**: Polls `/api/health`, `/api/workers`, `/api/queues`

### Goals

1. Replace HTTP polling with WebSocket push for real-time updates
2. Reduce API calls and improve responsiveness
3. Enable instant state change notifications (worker join/leave, task dispatch)
4. Maintain graceful fallback to HTTP polling if WebSocket unavailable

---

## 1. Textual WebSocket Integration Patterns

### 1.1 Textual Worker System

Textual provides a **Worker** system for background async tasks. WebSocket connection should run as a Worker:

```python
from textual.worker import Worker, get_current_worker
from textual.message import Message

class WebSocketWorker(Worker):
    """Background worker for WebSocket connection."""
    
    async def run(self) -> None:
        """Main worker loop - manages WebSocket connection."""
        while self._running:
            try:
                await self._connect_and_listen()
            except Exception:
                await self._reconnect_delay()
    
    async def _connect_and_listen(self) -> None:
        """Connect to WebSocket and process messages."""
        ws = await websockets.connect(self.url)
        self._connected = True
        
        async for message in ws:
            if not self._running:
                break
            # Parse and emit message to app
            data = json.loads(message)
            self.app.post_message(WSMessage(data))
```

### 1.2 Message-Based Event System

Textual uses a **Message** system for widget communication:

```python
from textual.message import Message

class WSWorkerJoined(Message):
    """Message: Worker joined the fleet."""
    def __init__(self, worker_id: str, role: str):
        super().__init__()
        self.worker_id = worker_id
        self.role = role

class WSWorkerLeft(Message):
    """Message: Worker left the fleet."""
    def __init__(self, worker_id: str):
        super().__init__()
        self.worker_id = worker_id

class WSWorkerStateChanged(Message):
    """Message: Worker state changed."""
    def __init__(self, worker_id: str, old_state: str, new_state: str):
        super().__init__()
        self.worker_id = worker_id
        self.old_state = old_state
        self.new_state = new_state

class WSTaskDispatched(Message):
    """Message: Task dispatched to worker."""
    def __init__(self, task_id: str, worker_id: str):
        super().__init__()
        self.task_id = task_id
        self.worker_id = worker_id

class WSHealthUpdate(Message):
    """Message: System health status update."""
    def __init__(self, health_data: dict):
        super().__init__()
        self.health_data = health_data

class WSQueueUpdate(Message):
    """Message: Queue shard status update."""
    def __init__(self, queues_data: list):
        super().__init__()
        self.queues_data = queues_data
```

### 1.3 Widget Message Handlers

Widgets receive messages via `on_<MessageClass>` methods:

```python
class WorkerTable(DataTable):
    """Widget that receives WebSocket messages."""
    
    def on_ws_worker_joined(self, message: WSWorkerJoined) -> None:
        """Handle worker join event."""
        self.add_worker(message.worker_id, "idle", message.role)
    
    def on_ws_worker_left(self, message: WSWorkerLeft) -> None:
        """Handle worker leave event."""
        self.remove_worker(message.worker_id)
    
    def on_ws_worker_state_changed(self, message: WSWorkerStateChanged) -> None:
        """Handle worker state change."""
        self.update_worker(message.worker_id, message.new_state)
```

---

## 2. WebSocket Client Class Design

### 2.1 Core WebSocket Client

```python
"""
Harness Lab TUI WebSocket Client.

Provides real-time WebSocket connection for Control Plane events.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Callable, Awaitable
from enum import Enum

import websockets
from websockets.client import WebSocketClientProtocol


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
    """
    url: str = "ws://localhost:4600/ws"
    reconnect_delay: float = 1.0
    max_reconnect_delay: float = 30.0
    ping_interval: float = 20.0
    ping_timeout: float = 20.0
    max_retries: int = 10
    message_timeout: float = 5.0


class TUIWebSocketClient:
    """Async WebSocket client for real-time Control Plane events.
    
    Features:
    - Automatic connection management
    - Exponential backoff reconnection
    - Message parsing and dispatch
    - Connection state tracking
    - Graceful degradation to HTTP polling
    
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
    """
    
    def __init__(self, config: WSConfig = None):
        """Initialize WebSocket client.
        
        Args:
            config: WebSocket configuration
        """
        self._config = config or WSConfig()
        self._ws: Optional[WebSocketClientProtocol] = None
        self._state: ConnectionState = ConnectionState.DISCONNECTED
        self._last_error: Optional[str] = None
        self._retry_count: int = 0
        self._running: bool = False
        self._message_handlers: list[Callable[[Dict], Awaitable[None]]] = []
        self._connection_handlers: list[Callable[[ConnectionState], Awaitable[None]]] = []
        
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
    
    def set_message_handler(self, handler: Callable[[Dict], Awaitable[None]]) -> None:
        """Set callback for received messages.
        
        Args:
            handler: Async function to process received messages
        """
        self._message_handlers.append(handler)
    
    def set_connection_handler(self, handler: Callable[[ConnectionState], Awaitable[None]]) -> None:
        """Set callback for connection state changes.
        
        Args:
            handler: Async function called on state changes
        """
        self._connection_handlers.append(handler)
    
    async def start(self) -> None:
        """Start WebSocket connection loop."""
        if self._running:
            return
        
        self._running = True
        self._retry_count = 0
        await self._connection_loop()
    
    async def stop(self) -> None:
        """Stop WebSocket connection."""
        self._running = False
        
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        
        self._state = ConnectionState.DISCONNECTED
        await self._notify_connection_handlers(ConnectionState.DISCONNECTED)
    
    async def _connection_loop(self) -> None:
        """Main connection loop with reconnect logic."""
        while self._running:
            try:
                await self._connect()
                await self._listen()
            except Exception as e:
                self._last_error = str(e)
                self._state = ConnectionState.ERROR
                
                if not self._running:
                    break
                
                await self._reconnect()
    
    async def _connect(self) -> None:
        """Establish WebSocket connection."""
        self._state = ConnectionState.CONNECTING
        await self._notify_connection_handlers(ConnectionState.CONNECTING)
        
        self._ws = await websockets.connect(
            self._config.url,
            ping_interval=self._config.ping_interval,
            ping_timeout=self._config.ping_timeout,
        )
        
        self._state = ConnectionState.CONNECTED
        self._retry_count = 0
        self._last_error = None
        
        await self._notify_connection_handlers(ConnectionState.CONNECTED)
    
    async def _listen(self) -> None:
        """Listen for WebSocket messages."""
        if not self._ws:
            raise RuntimeError("WebSocket not connected")
        
        async for message in self._ws:
            if not self._running:
                break
            
            try:
                data = json.loads(message)
                await self._dispatch_message(data)
            except json.JSONDecodeError as e:
                self._last_error = f"JSON decode error: {e}"
            except Exception as e:
                self._last_error = f"Message handling error: {e}"
    
    async def _dispatch_message(self, data: Dict) -> None:
        """Dispatch message to handlers."""
        for handler in self._message_handlers:
            try:
                await asyncio.wait_for(
                    handler(data),
                    timeout=self._config.message_timeout
                )
            except asyncio.TimeoutError:
                self._last_error = "Message handler timeout"
            except Exception as e:
                self._last_error = f"Handler error: {e}"
    
    async def _reconnect(self) -> None:
        """Handle reconnection with exponential backoff."""
        self._retry_count += 1
        self._state = ConnectionState.RECONNECTING
        await self._notify_connection_handlers(ConnectionState.RECONNECTING)
        
        if self._retry_count > self._config.max_retries:
            # Max retries reached - should fallback to HTTP polling
            self._state = ConnectionState.ERROR
            await self._notify_connection_handlers(ConnectionState.ERROR)
            return
        
        # Exponential backoff with cap
        delay = min(
            self._config.reconnect_delay * (2 ** (self._retry_count - 1)),
            self._config.max_reconnect_delay
        )
        
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
            data: Message data to send
            
        Returns:
            True if sent successfully
        """
        if not self._ws or not self.is_connected:
            return False
        
        try:
            await self._ws.send(json.dumps(data))
            return True
        except Exception as e:
            self._last_error = f"Send error: {e}"
            return False
```

### 2.2 Textual Worker Integration

```python
"""
WebSocket Worker for Textual TUI.

Bridges WebSocket client to Textual's Worker and Message systems.
"""

from textual.worker import Worker, get_current_worker
from textual.message import Message

from ..ws_client import TUIWebSocketClient, WSConfig, ConnectionState


class WSMessage(Message):
    """Base WebSocket message."""
    
    def __init__(self, data: dict):
        super().__init__()
        self.data = data
        self.event_type = data.get("event_type", "unknown")
        self.timestamp = data.get("timestamp", time.time())


class WebSocketWorker(Worker):
    """Textual Worker that manages WebSocket connection.
    
    This worker:
    1. Runs WebSocket client in background
    2. Parses incoming messages
    3. Posts appropriate Message to the app
    
    Usage in DashboardScreen:
        def on_mount(self):
            self._ws_worker = WebSocketWorker(
                WSConfig(url=self.ws_url),
                message_handler=self._handle_ws_message
            )
            self._ws_worker.start()
    
    def on_ws_message(self, message: WSMessage):
        # Handle typed messages based on event_type
        event_type = message.event_type
        # ...
    """
    
    def __init__(
        self,
        config: WSConfig,
        app_ref: "App" = None,
        **kwargs
    ):
        super().__init__(**kwargs)
        self._config = config
        self._app = app_ref
        self._client: Optional[TUIWebSocketClient] = None
    
    async def run(self) -> None:
        """Worker main loop."""
        self._client = TUIWebSocketClient(self._config)
        
        # Set message handler
        self._client.set_message_handler(self._handle_message)
        
        # Set connection handler
        self._client.set_connection_handler(self._handle_connection)
        
        try:
            await self._client.start()
        finally:
            await self._client.stop()
    
    async def _handle_message(self, data: dict) -> None:
        """Process WebSocket message and post to app."""
        # Post generic message
        if self._app:
            self._app.post_message(WSMessage(data))
    
    async def _handle_connection(self, state: ConnectionState) -> None:
        """Handle connection state changes."""
        if self._app:
            self._app.post_message(WSConnectionState(state))
    
    def stop_worker(self) -> None:
        """Stop the worker."""
        if self._client:
            asyncio.create_task(self._client.stop())
        self.cancel()
```

---

## 3. Backend WebSocket Endpoints

### 3.1 FastAPI WebSocket Router

```python
"""
Control Plane WebSocket Router.

Provides real-time event streaming via WebSocket.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import Dict, Any, List
import asyncio
import json
from datetime import datetime

router = APIRouter(tags=["websocket"])


class ConnectionManager:
    """Manage active WebSocket connections."""
    
    def __init__(self):
        self._connections: List[WebSocket] = []
        self._lock = asyncio.Lock()
    
    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register new connection."""
        await websocket.accept()
        async with self._lock:
            self._connections.append(websocket)
    
    async def disconnect(self, websocket: WebSocket) -> None:
        """Remove connection."""
        async with self._lock:
            if websocket in self._connections:
                self._connections.remove(websocket)
    
    async def broadcast(self, message: Dict[str, Any]) -> None:
        """Broadcast message to all connections."""
        async with self._lock:
            disconnected = []
            for ws in self._connections:
                try:
                    await ws.send_json(message)
                except Exception:
                    disconnected.append(ws)
            
            # Clean up disconnected
            for ws in disconnected:
                self._connections.remove(ws)
    
    async def send_to(self, websocket: WebSocket, message: Dict[str, Any]) -> None:
        """Send message to specific connection."""
        try:
            await websocket.send_json(message)
        except Exception:
            await self.disconnect(websocket)


manager = ConnectionManager()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Main WebSocket endpoint for TUI real-time updates.
    
    Protocol:
    1. Client connects
    2. Server sends initial state snapshot
    3. Server streams events as they occur
    
    Message format:
    {
        "event_type": "worker_joined|worker_left|worker_state|task_dispatched|...",
        "timestamp": float,
        "data": {...}
    }
    """
    await manager.connect(websocket)
    
    try:
        # Send initial state snapshot
        initial_state = await get_initial_state()
        await websocket.send_json({
            "event_type": "initial_state",
            "timestamp": datetime.now().timestamp(),
            "data": initial_state
        })
        
        # Listen for client messages (optional commands)
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_json(),
                    timeout=60.0  # Heartbeat interval
                )
                
                # Handle client commands
                command = data.get("command")
                if command:
                    await handle_client_command(websocket, command, data)
                    
            except asyncio.TimeoutError:
                # Send heartbeat
                await websocket.send_json({
                    "event_type": "heartbeat",
                    "timestamp": datetime.now().timestamp()
                })
                
    except WebSocketDisconnect:
        await manager.disconnect(websocket)


async def get_initial_state() -> Dict[str, Any]:
    """Get initial state snapshot for new connection."""
    from ..runtime import get_runtime
    
    runtime = get_runtime()
    
    return {
        "health": {
            "status": "healthy",
            "postgres_ready": True,
            "redis_ready": True,
            "docker_ready": True,
        },
        "workers": [
            {
                "worker_id": w.worker_id,
                "state": w.state,
                "role_profile": w.role_profile,
                "current_run_id": w.current_run_id,
            }
            for w in runtime.list_workers()
        ],
        "queues": runtime.get_queue_status(),
    }


async def handle_client_command(websocket: WebSocket, command: str, data: Dict) -> None:
    """Handle client command messages."""
    # Commands: subscribe, unsubscribe, ping
    if command == "ping":
        await websocket.send_json({
            "event_type": "pong",
            "timestamp": datetime.now().timestamp()
        })
    elif command == "subscribe":
        # Subscribe to specific event types
        # TODO: Implement subscription filtering
        pass


# === Event Broadcasting Functions ===
# Called by Control Plane when events occur

async def broadcast_worker_joined(worker_id: str, role: str) -> None:
    """Broadcast worker join event."""
    await manager.broadcast({
        "event_type": "worker_joined",
        "timestamp": datetime.now().timestamp(),
        "data": {
            "worker_id": worker_id,
            "role": role,
            "state": "idle"
        }
    })


async def broadcast_worker_left(worker_id: str) -> None:
    """Broadcast worker leave event."""
    await manager.broadcast({
        "event_type": "worker_left",
        "timestamp": datetime.now().timestamp(),
        "data": {
            "worker_id": worker_id
        }
    })


async def broadcast_worker_state_change(
    worker_id: str,
    old_state: str,
    new_state: str,
    task_id: str = None
) -> None:
    """Broadcast worker state change."""
    await manager.broadcast({
        "event_type": "worker_state_changed",
        "timestamp": datetime.now().timestamp(),
        "data": {
            "worker_id": worker_id,
            "old_state": old_state,
            "new_state": new_state,
            "task_id": task_id
        }
    })


async def broadcast_task_dispatched(task_id: str, worker_id: str) -> None:
    """Broadcast task dispatch event."""
    await manager.broadcast({
        "event_type": "task_dispatched",
        "timestamp": datetime.now().timestamp(),
        "data": {
            "task_id": task_id,
            "worker_id": worker_id
        }
    })


async def broadcast_task_completed(task_id: str, worker_id: str, success: bool) -> None:
    """Broadcast task completion event."""
    await manager.broadcast({
        "event_type": "task_completed",
        "timestamp": datetime.now().timestamp(),
        "data": {
            "task_id": task_id,
            "worker_id": worker_id,
            "success": success
        }
    })


async def broadcast_health_update(health_data: Dict) -> None:
    """Broadcast health status update."""
    await manager.broadcast({
        "event_type": "health_update",
        "timestamp": datetime.now().timestamp(),
        "data": health_data
    })


async def broadcast_queue_update(queues_data: List[Dict]) -> None:
    """Broadcast queue status update."""
    await manager.broadcast({
        "event_type": "queue_update",
        "timestamp": datetime.now().timestamp(),
        "data": queues_data
    })
```

### 3.2 Integration with Runtime

```python
"""
Hooks for WebSocket broadcasting in Control Plane runtime.
"""

from ..control_plane.websocket import (
    broadcast_worker_joined,
    broadcast_worker_left,
    broadcast_worker_state_change,
    broadcast_task_dispatched,
    broadcast_task_completed,
)


class RuntimeWithWebSocket:
    """Runtime that broadcasts events via WebSocket."""
    
    def register_worker(self, worker: Worker) -> None:
        """Register worker and broadcast event."""
        self._workers[worker.worker_id] = worker
        
        # Broadcast join event
        asyncio.create_task(
            broadcast_worker_joined(worker.worker_id, worker.role_profile)
        )
    
    def unregister_worker(self, worker_id: str) -> None:
        """Unregister worker and broadcast event."""
        if worker_id in self._workers:
            del self._workers[worker_id]
            
            # Broadcast leave event
            asyncio.create_task(broadcast_worker_left(worker_id))
    
    def transition_worker_state(
        self,
        worker_id: str,
        new_state: str,
        task_id: str = None
    ) -> None:
        """Transition worker state and broadcast."""
        worker = self._workers.get(worker_id)
        if worker:
            old_state = worker.state
            worker.state = new_state
            
            # Broadcast state change
            asyncio.create_task(
                broadcast_worker_state_change(worker_id, old_state, new_state, task_id)
            )
    
    def dispatch_task(self, task_id: str, worker_id: str) -> None:
        """Dispatch task and broadcast event."""
        # ... existing dispatch logic ...
        
        # Broadcast dispatch
        asyncio.create_task(broadcast_task_dispatched(task_id, worker_id))
    
    def complete_task(self, task_id: str, worker_id: str, success: bool) -> None:
        """Complete task and broadcast event."""
        # ... existing completion logic ...
        
        # Broadcast completion
        asyncio.create_task(broadcast_task_completed(task_id, worker_id, success))
```

---

## 4. UI Update Trigger Mechanism

### 4.1 Message-Based Widget Updates

The Dashboard screen handles WebSocket messages and dispatches to widgets:

```python
"""
Dashboard with WebSocket support.
"""

from textual.screen import Screen
from textual.message import Message

from ..ws_worker import WebSocketWorker, WSConfig
from ..ws_client import ConnectionState


class WSConnectionState(Message):
    """WebSocket connection state message."""
    
    def __init__(self, state: ConnectionState):
        super().__init__()
        self.state = state


class DashboardScreenWS(Screen):
    """Dashboard with WebSocket real-time updates."""
    
    def __init__(self, theme, api_url, ws_url=None, **kwargs):
        super().__init__(**kwargs)
        self.theme = theme
        self.api_url = api_url
        self.ws_url = ws_url or api_url.replace("http://", "ws://").replace("/api", "/ws")
        
        # WebSocket worker
        self._ws_worker: Optional[WebSocketWorker] = None
        
        # HTTP client fallback
        self._http_client: Optional[ControlPlaneClient] = None
        
        # Connection mode
        self._ws_connected = False
    
    def on_mount(self) -> None:
        """Initialize WebSocket and fallback HTTP client."""
        # Start WebSocket worker
        self._ws_worker = WebSocketWorker(
            WSConfig(url=self.ws_url),
            app_ref=self.app
        )
        self.run_worker(self._ws_worker)
        
        # Initialize HTTP client as fallback
        self._http_client = ControlPlaneClient(APIConfig(base_url=self.api_url))
        asyncio.create_task(self._http_client.connect())
        
        # Start polling fallback (interval longer than WebSocket heartbeat)
        self.set_interval(30.0, self._poll_fallback)
    
    def on_unmount(self) -> None:
        """Cleanup."""
        if self._ws_worker:
            self._ws_worker.stop_worker()
        
        if self._http_client:
            asyncio.create_task(self._http_client.disconnect())
    
    # === WebSocket Message Handlers ===
    
    def on_ws_connection_state(self, message: WSConnectionState) -> None:
        """Handle WebSocket connection state change."""
        state = message.state
        
        events = self.query_one("#event-stream", EventStream)
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        if state == ConnectionState.CONNECTED:
            self._ws_connected = True
            events.add_event(timestamp, "CONNECT", "WebSocket connected")
        elif state == ConnectionState.DISCONNECTED:
            self._ws_connected = False
            events.add_event(timestamp, "DISCONNECT", "WebSocket disconnected")
        elif state == ConnectionState.RECONNECTING:
            events.add_event(timestamp, "RECONNECT", "WebSocket reconnecting...")
        elif state == ConnectionState.ERROR:
            self._ws_connected = False
            events.add_event(timestamp, "ERROR", "WebSocket error - using HTTP fallback")
    
    def on_ws_message(self, message: WSMessage) -> None:
        """Handle WebSocket message."""
        event_type = message.event_type
        data = message.data
        
        # Dispatch based on event type
        handler_map = {
            "initial_state": self._handle_initial_state,
            "worker_joined": self._handle_worker_joined,
            "worker_left": self._handle_worker_left,
            "worker_state_changed": self._handle_worker_state_changed,
            "task_dispatched": self._handle_task_dispatched,
            "task_completed": self._handle_task_completed,
            "health_update": self._handle_health_update,
            "queue_update": self._handle_queue_update,
            "heartbeat": self._handle_heartbeat,
        }
        
        handler = handler_map.get(event_type)
        if handler:
            handler(data)
    
    def _handle_initial_state(self, data: dict) -> None:
        """Handle initial state snapshot."""
        # Update services
        health = data.get("health", {})
        self._update_services(health)
        
        # Update workers
        workers = data.get("workers", [])
        self._sync_workers(workers)
        
        # Update queues
        queues = data.get("queues", [])
        self._update_queues(queues)
        
        # Log event
        events = self.query_one("#event-stream", EventStream)
        events.add_event(
            datetime.now().strftime("%H:%M:%S"),
            "INIT",
            f"Loaded {len(workers)} workers"
        )
    
    def _handle_worker_joined(self, data: dict) -> None:
        """Handle worker join event."""
        worker_id = data.get("worker_id")
        role = data.get("role", "general")
        state = data.get("state", "idle")
        
        workers_table = self.query_one("#worker-table", WorkerTable)
        workers_table.add_worker(worker_id, state, role)
        
        events = self.query_one("#event-stream", EventStream)
        events.add_event(
            datetime.now().strftime("%H:%M:%S"),
            "REGISTER",
            f"{worker_id} ({role}) joined"
        )
        
        self._workers_cache[worker_id] = {"state": state, "role": role}
    
    def _handle_worker_left(self, data: dict) -> None:
        """Handle worker leave event."""
        worker_id = data.get("worker_id")
        
        workers_table = self.query_one("#worker-table", WorkerTable)
        workers_table.remove_worker(worker_id)
        
        events = self.query_one("#event-stream", EventStream)
        events.add_event(
            datetime.now().strftime("%H:%M:%S"),
            "OFFLINE",
            f"{worker_id} left"
        )
        
        self._workers_cache.pop(worker_id, None)
    
    def _handle_worker_state_changed(self, data: dict) -> None:
        """Handle worker state change."""
        worker_id = data.get("worker_id")
        old_state = data.get("old_state")
        new_state = data.get("new_state")
        task_id = data.get("task_id")
        
        workers_table = self.query_one("#worker-table", WorkerTable)
        workers_table.update_worker(worker_id, new_state, task_id)
        
        events = self.query_one("#event-stream", EventStream)
        events.add_event(
            datetime.now().strftime("%H:%M:%S"),
            "STATE",
            f"{worker_id}: {old_state} → {new_state}"
        )
        
        if worker_id in self._workers_cache:
            self._workers_cache[worker_id]["state"] = new_state
    
    def _handle_task_dispatched(self, data: dict) -> None:
        """Handle task dispatch event."""
        task_id = data.get("task_id")
        worker_id = data.get("worker_id")
        
        events = self.query_one("#event-stream", EventStream)
        events.add_event(
            datetime.now().strftime("%H:%M:%S"),
            "DISPATCH",
            f"{task_id} → {worker_id}"
        )
    
    def _handle_task_completed(self, data: dict) -> None:
        """Handle task completion event."""
        task_id = data.get("task_id")
        worker_id = data.get("worker_id")
        success = data.get("success", True)
        
        events = self.query_one("#event-stream", EventStream)
        event_type = "COMPLETE" if success else "FAIL"
        events.add_event(
            datetime.now().strftime("%H:%M:%S"),
            event_type,
            f"{task_id} on {worker_id}"
        )
    
    def _handle_health_update(self, data: dict) -> None:
        """Handle health status update."""
        self._update_services(data)
    
    def _handle_queue_update(self, data: dict) -> None:
        """Handle queue status update."""
        self._update_queues(data)
    
    def _handle_heartbeat(self, data: dict) -> None:
        """Handle WebSocket heartbeat."""
        # Heartbeat received - connection alive
        pass
    
    # === Fallback Polling ===
    
    async def _poll_fallback(self) -> None:
        """HTTP polling fallback when WebSocket disconnected."""
        if self._ws_connected:
            return  # WebSocket connected, skip polling
        
        if not self._http_client or not self._http_client.is_connected:
            # Try to reconnect HTTP
            await self._http_client.connect()
            if not self._http_client.is_connected:
                return
        
        try:
            # Fetch via HTTP
            health = await self._http_client.get_health()
            workers = await self._http_client.list_workers()
            queues = await self._http_client.get_queues()
            
            self._update_services(health)
            self._sync_workers(workers)
            self._update_queues(queues)
            
        except Exception as e:
            events = self.query_one("#event-stream", EventStream)
            events.add_event(
                datetime.now().strftime("%H:%M:%S"),
                "ERROR",
                f"HTTP fallback error: {e}"
            )
```

---

## 5. Error Handling and Reconnection Strategy

### 5.1 Connection States

```python
class ConnectionState(Enum):
    DISCONNECTED = "disconnected"     # Initial state / stopped
    CONNECTING = "connecting"         # Attempting connection
    CONNECTED = "connected"           # Active connection
    RECONNECTING = "reconnecting"     # Connection lost, retrying
    ERROR = "error"                   # Max retries exhausted
```

### 5.2 Reconnection Flow

```
┌──────────────────────────────────────────────────────────────────┐
│                    WebSocket Connection Flow                      │
└──────────────────────────────────────────────────────────────────┐

[DISCONNECTED] ──► start() ──► [CONNECTING]
                                    │
                                    ├──► Success ──► [CONNECTED]
                                    │                      │
                                    │                      ├──► Listen loop
                                    │                      │
                                    │                      └──► Error ──► [RECONNECTING]
                                    │                                              │
                                    ├──► Fail ──► [RECONNECTING]                   │
                                    │              │                               │
                                    │              ├──► retry_count++              │
                                    │              │                               │
                                    │              ├──► delay = exp_backoff()      │
                                    │              │                               │
                                    │              └──► sleep(delay)               │
                                    │                              │               │
                                    │                              └──► [CONNECTING]
                                    │                                              │
                                    └──────────────────────────────────────────────┤
                                    │                                              │
                                    └──────────► retry_count > max_retries ──► [ERROR]
                                        │                                          │
                                        └──────────────────► Fallback to HTTP polling
```

### 5.3 Error Handling Matrix

| Error Type | Action | Retry | Fallback |
|-----------|--------|-------|----------|
| Connection refused | Reconnect | Yes | After max retries |
| Timeout | Reconnect | Yes | After max retries |
| JSON decode error | Log, continue | No | No |
| Handler timeout | Log, continue | No | No |
| WebSocket closed | Reconnect | Yes | After max retries |
| Network error | Reconnect | Yes | After max retries |

### 5.4 Graceful Degradation

```python
class HybridClient:
    """Hybrid WebSocket + HTTP client with graceful fallback."""
    
    def __init__(self, ws_config: WSConfig, http_config: APIConfig):
        self._ws_client = TUIWebSocketClient(ws_config)
        self._http_client = ControlPlaneClient(http_config)
        self._mode = "websocket"  # websocket | http | hybrid
    
    async def start(self) -> None:
        """Start with WebSocket, fallback to HTTP."""
        # Set connection handler to trigger fallback
        self._ws_client.set_connection_handler(self._on_ws_state)
        
        # Start WebSocket
        await self._ws_client.start()
        
        # Also start HTTP client (for operations that need HTTP)
        await self._http_client.connect()
    
    async def _on_ws_state(self, state: ConnectionState) -> None:
        """Handle WebSocket state changes."""
        if state == ConnectionState.CONNECTED:
            self._mode = "websocket"
        elif state == ConnectionState.ERROR:
            self._mode = "http"
            # Start polling interval
            self._start_http_polling()
        elif state == ConnectionState.RECONNECTING:
            self._mode = "hybrid"  # Use HTTP while reconnecting
    
    async def drain_worker(self, worker_id: str) -> dict:
        """Operation via HTTP (WebSocket for notifications only)."""
        # Drain operation always via HTTP
        result = await self._http_client.drain_worker(worker_id)
        
        # WebSocket will broadcast state change notification
        return result
```

---

## 6. Integration with Existing api_client.py

### 6.1 Enhanced API Client with WebSocket

```python
"""
Enhanced API Client with WebSocket support.

Maintains HTTP client for operations, adds WebSocket for events.
"""

from __future__ import annotations

from .api_client import ControlPlaneClient, APIConfig
from .ws_client import TUIWebSocketClient, WSConfig, ConnectionState


class EnhancedAPIClient:
    """API client with HTTP operations + WebSocket events.
    
    Architecture:
    - HTTP: Used for operations (drain, resume, get details)
    - WebSocket: Used for real-time event notifications
    
    Usage:
        client = EnhancedAPIClient(
            APIConfig(base_url="http://localhost:4600"),
            WSConfig(url="ws://localhost:4600/ws")
        )
        await client.connect()
        
        # HTTP operations
        result = await client.drain_worker("w-01")
        
        # WebSocket events handled via callbacks
        client.set_event_handler(on_worker_event)
    """
    
    def __init__(
        self,
        http_config: APIConfig = None,
        ws_config: WSConfig = None,
    ):
        self._http = ControlPlaneClient(http_config or APIConfig())
        self._ws = TUIWebSocketClient(ws_config or WSConfig())
        
        self._ws_connected = False
        self._http_connected = False
    
    @property
    def is_connected(self) -> bool:
        """Check if at least one transport is connected."""
        return self._ws_connected or self._http_connected
    
    @property
    def mode(self) -> str:
        """Get current connection mode."""
        if self._ws_connected and self._http_connected:
            return "full"
        elif self._ws_connected:
            return "websocket_only"
        elif self._http_connected:
            return "http_only"
        else:
            return "disconnected"
    
    async def connect(self) -> bool:
        """Connect both transports."""
        # Connect HTTP first (operations)
        http_ok = await self._http.connect()
        self._http_connected = http_ok
        
        # Connect WebSocket (events)
        self._ws.set_connection_handler(self._handle_ws_state)
        self._ws.set_message_handler(self._handle_ws_message)
        await self._ws.start()
        
        return self.is_connected
    
    async def disconnect(self) -> None:
        """Disconnect both transports."""
        await self._ws.stop()
        await self._http.disconnect()
        
        self._ws_connected = False
        self._http_connected = False
    
    def set_event_handler(self, handler: Callable[[dict], Awaitable]) -> None:
        """Set handler for WebSocket events."""
        self._ws.set_message_handler(handler)
    
    # === HTTP Operations (unchanged) ===
    
    async def drain_worker(self, worker_id: str, reason: str = None) -> dict:
        """Drain worker via HTTP."""
        return await self._http.drain_worker(worker_id, reason)
    
    async def resume_worker(self, worker_id: str) -> dict:
        """Resume worker via HTTP."""
        return await self._http.resume_worker(worker_id)
    
    async def get_worker(self, worker_id: str) -> dict:
        """Get worker details via HTTP."""
        return await self._http.get_worker(worker_id)
    
    async def get_health(self) -> dict:
        """Get health via HTTP (or cached from WS)."""
        if self._ws_connected:
            # WebSocket provides real-time health
            # Could cache latest health_update
            pass
        return await self._http.get_health()
    
    async def list_workers(self) -> list:
        """List workers via HTTP (or cached from WS)."""
        if self._ws_connected:
            # WebSocket provides worker_joined/left events
            # Could maintain live worker list
            pass
        return await self._http.list_workers()
    
    # === WebSocket Handlers ===
    
    async def _handle_ws_state(self, state: ConnectionState) -> None:
        """Handle WebSocket connection state."""
        self._ws_connected = (state == ConnectionState.CONNECTED)
    
    async def _handle_ws_message(self, data: dict) -> None:
        """Handle WebSocket message (forward to app)."""
        # This is called by TUI app to process events
        pass


def create_enhanced_client(
    api_url: str = "http://localhost:4600",
    ws_url: str = None,
    **kwargs
) -> EnhancedAPIClient:
    """Create enhanced API client with WebSocket.
    
    Args:
        api_url: HTTP API base URL
        ws_url: WebSocket URL (default: derived from api_url)
        
    Returns:
        EnhancedAPIClient instance
    """
    http_config = APIConfig(base_url=api_url, **kwargs)
    
    # Derive WebSocket URL
    if ws_url is None:
        ws_url = api_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = ws_url.rstrip("/") + "/ws"
    
    ws_config = WSConfig(url=ws_url)
    
    return EnhancedAPIClient(http_config, ws_config)
```

---

## 7. Code Example: Complete Dashboard Integration

```python
"""
Complete Dashboard Screen with WebSocket integration.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Dict, Optional

from textual.screen import Screen
from textual.widgets import Header
from textual.containers import Container, Horizontal
from textual.app import ComposeResult
from textual.reactive import reactive

from ..widgets import ServicePanel, WorkerTable, StatusBar, EventStream, QueuePanel
from ..theme import ColorTheme
from ..enhanced_client import EnhancedAPIClient, create_enhanced_client
from ..ws_client import ConnectionState
from ..ws_worker import WSMessage, WSConnectionState


class DashboardScreen(Screen):
    """Dashboard with WebSocket real-time updates + HTTP fallback.
    
    Connection Strategy:
    1. WebSocket for real-time events (worker changes, task dispatches)
    2. HTTP polling fallback (30s interval) when WebSocket disconnected
    3. HTTP for operations (drain, resume, get details)
    
    Features:
    - Instant worker state updates via WebSocket
    - Real-time task dispatch notifications
    - Live queue depth updates
    - Automatic reconnect with exponential backoff
    - Graceful degradation to HTTP polling
    """
    
    DEFAULT_CSS = """
    /* Same CSS as original */
    """
    
    BINDINGS = [
        ("ctrl+h", "show_help", "Help"),
        ("escape", "quit", "Exit"),
        ("d", "drain_worker", "Drain"),
        ("r", "resume_worker", "Resume"),
        ("i", "inspect_worker", "Inspect"),
        ("q", "quit", "Quit"),
    ]
    
    workers_count: reactive[int] = reactive(0)
    
    def __init__(
        self,
        theme: ColorTheme = None,
        api_url: str = "http://localhost:4600",
        ws_url: str = None,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.theme = theme or ColorTheme.dark()
        self.api_url = api_url
        self.ws_url = ws_url
        
        # Enhanced client (HTTP + WebSocket)
        self._client: Optional[EnhancedAPIClient] = None
        
        # Worker cache for state tracking
        self._workers_cache: Dict[str, Dict] = {}
        
        # Connection state
        self._ws_connected = False
        self._http_connected = False
    
    def compose(self) -> ComposeResult:
        """Compose dashboard layout."""
        yield Header(id="header")
        
        with Container(id="main"):
            with Horizontal(id="top-panels"):
                yield ServicePanel(self.theme, id="service-panel")
                yield QueuePanel(self.theme, id="queue-panel")
                yield WorkerTable(self.theme, id="worker-table")
            
            yield EventStream(self.theme, id="event-stream")
        
        yield StatusBar(self.theme, id="status-bar")
    
    def on_mount(self) -> None:
        """Initialize enhanced client."""
        header = self.query_one("#header", Header)
        header.title = "Harness Lab Control Plane"
        
        # Create enhanced client
        self._client = create_enhanced_client(
            api_url=self.api_url,
            ws_url=self.ws_url
        )
        
        # Set WebSocket event handler
        self._client.set_event_handler(self._handle_ws_event)
        
        # Connect (async)
        asyncio.create_task(self._connect_client())
        
        # HTTP fallback polling (30s interval)
        self.set_interval(30.0, self._poll_fallback)
    
    def on_unmount(self) -> None:
        """Cleanup."""
        if self._client:
            asyncio.create_task(self._client.disconnect())
    
    async def _connect_client(self) -> None:
        """Connect enhanced client."""
        connected = await self._client.connect()
        
        events = self.query_one("#event-stream", EventStream)
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        if connected:
            events.add_event(timestamp, "CONNECT", f"Connected ({self._client.mode})")
        else:
            events.add_event(timestamp, "ERROR", "Connection failed - using demo data")
            self._init_demo_data()
    
    async def _handle_ws_event(self, data: dict) -> None:
        """Handle WebSocket event."""
        event_type = data.get("event_type")
        event_data = data.get("data", {})
        
        events = self.query_one("#event-stream", EventStream)
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        # Dispatch event type
        if event_type == "initial_state":
            self._handle_initial_state(event_data)
            events.add_event(timestamp, "INIT", "State loaded from WebSocket")
        
        elif event_type == "worker_joined":
            self._handle_worker_joined(event_data)
            events.add_event(timestamp, "REGISTER", f"{event_data['worker_id']} joined")
        
        elif event_type == "worker_left":
            self._handle_worker_left(event_data)
            events.add_event(timestamp, "OFFLINE", f"{event_data['worker_id']} left")
        
        elif event_type == "worker_state_changed":
            self._handle_worker_state_changed(event_data)
            old, new = event_data['old_state'], event_data['new_state']
            events.add_event(timestamp, "STATE", f"{event_data['worker_id']}: {old}→{new}")
        
        elif event_type == "task_dispatched":
            events.add_event(timestamp, "DISPATCH", f"{event_data['task_id']}→{event_data['worker_id']}")
        
        elif event_type == "task_completed":
            event = "COMPLETE" if event_data.get("success") else "FAIL"
            events.add_event(timestamp, event, f"{event_data['task_id']} on {event_data['worker_id']}")
        
        elif event_type == "health_update":
            self._update_services(event_data)
        
        elif event_type == "queue_update":
            self._update_queues(event_data)
    
    def _handle_initial_state(self, data: dict) -> None:
        """Handle initial state snapshot."""
        health = data.get("health", {})
        workers = data.get("workers", [])
        queues = data.get("queues", [])
        
        self._update_services(health)
        
        workers_table = self.query_one("#worker-table", WorkerTable)
        workers_table.clear_workers()
        
        for w in workers:
            workers_table.add_worker(
                w.get("worker_id"),
                w.get("state", "idle"),
                w.get("role_profile", "general"),
                w.get("current_run_id") or "-",
                "-"
            )
            self._workers_cache[w.get("worker_id")] = w
        
        self._update_queues(queues)
        self.workers_count = len(workers)
    
    def _handle_worker_joined(self, data: dict) -> None:
        """Handle worker join."""
        worker_id = data.get("worker_id")
        role = data.get("role", "general")
        state = data.get("state", "idle")
        
        workers_table = self.query_one("#worker-table", WorkerTable)
        workers_table.add_worker(worker_id, state, role)
        
        self._workers_cache[worker_id] = {"state": state, "role": role}
        self.workers_count = len(self._workers_cache)
    
    def _handle_worker_left(self, data: dict) -> None:
        """Handle worker leave."""
        worker_id = data.get("worker_id")
        
        workers_table = self.query_one("#worker-table", WorkerTable)
        workers_table.remove_worker(worker_id)
        
        self._workers_cache.pop(worker_id, None)
        self.workers_count = len(self._workers_cache)
    
    def _handle_worker_state_changed(self, data: dict) -> None:
        """Handle worker state change."""
        worker_id = data.get("worker_id")
        new_state = data.get("new_state")
        task_id = data.get("task_id")
        
        workers_table = self.query_one("#worker-table", WorkerTable)
        workers_table.update_worker(worker_id, new_state, task_id)
        
        if worker_id in self._workers_cache:
            self._workers_cache[worker_id]["state"] = new_state
    
    def _update_services(self, health: dict) -> None:
        """Update service panel."""
        services = self.query_one("#service-panel", ServicePanel)
        services.update_all({
            "PostgreSQL 16": "running" if health.get("postgres_ready") else "stopped",
            "Redis 7": "running" if health.get("redis_ready") else "stopped",
            "Docker CE": "running" if health.get("docker_ready") else "stopped",
            "API": "running" if health.get("status") == "healthy" else "warning",
        })
    
    def _update_queues(self, queues: list) -> None:
        """Update queue panel."""
        queues_panel = self.query_one("#queue-panel", QueuePanel)
        queues_panel.update_shards(queues)
    
    async def _poll_fallback(self) -> None:
        """HTTP polling fallback."""
        if self._client and self._client.mode in ("full", "websocket_only"):
            return  # WebSocket connected, skip
        
        if not self._client or not self._client._http_connected:
            return
        
        try:
            health = await self._client._http.get_health()
            workers = await self._client._http.list_workers()
            queues = await self._client._http.get_queues()
            
            self._update_services(health)
            # Sync workers...
            self._update_queues(queues)
            
        except Exception as e:
            events = self.query_one("#event-stream", EventStream)
            events.add_event(
                datetime.now().strftime("%H:%M:%S"),
                "ERROR",
                f"HTTP fallback error: {e}"
            )
    
    # === Actions ===
    
    async def action_drain_worker(self) -> None:
        """Drain selected worker via HTTP."""
        # ... same as original, uses self._client.drain_worker() ...
    
    async def action_resume_worker(self) -> None:
        """Resume selected worker via HTTP."""
        # ... same as original ...
    
    def action_inspect_worker(self) -> None:
        """Inspect selected worker."""
        # ... same as original ...
    
    def action_quit(self) -> None:
        """Quit."""
        self.app.exit()
```

---

## 8. Best Practices Summary

### 8.1 Textual WebSocket Integration

| Practice | Reason |
|----------|--------|
| Use Worker for WebSocket | Background task that doesn't block UI |
| Post Message to App | Decouples WebSocket from widgets |
| Widget `on_<Message>` handlers | Clean event handling pattern |
| Reactive properties | Auto-update UI on state changes |
| set_interval for fallback | Periodic HTTP polling when WS disconnected |

### 8.2 Connection Management

| Practice | Reason |
|----------|--------|
| Exponential backoff | Avoid connection spam |
| Max retry limit | Fall back to HTTP after exhaustion |
| State tracking | Enable UI feedback on connection status |
| Dual transport (HTTP + WS) | Operations via HTTP, events via WS |

### 8.3 Error Handling

| Practice | Reason |
|----------|--------|
| Graceful degradation | HTTP fallback ensures functionality |
| Log errors to EventStream | User sees connection issues |
| Auto-reconnect | Transparent recovery from transient errors |
| Heartbeat detection | Know if connection is alive |

---

## 9. Implementation Roadmap

### Phase 1: WebSocket Client
- Create `ws_client.py` with TUIWebSocketClient
- Create `ws_worker.py` with WebSocketWorker
- Add to TUI package

### Phase 2: Backend Endpoints
- Create `control_plane/websocket.py` router
- Add WebSocket endpoint `/ws`
- Implement ConnectionManager
- Add event broadcast functions

### Phase 3: Runtime Hooks
- Integrate broadcast functions into Runtime
- Hook register_worker, unregister_worker
- Hook state transitions, dispatch, completion

### Phase 4: Dashboard Integration
- Update DashboardScreen to use EnhancedAPIClient
- Add WebSocket message handlers
- Add HTTP fallback polling
- Test with both transports

### Phase 5: Polish
- Add connection status indicator
- Add reconnect UI feedback
- Add subscription filtering (optional)
- Performance testing

---

## 10. Dependencies

```toml
# pyproject.toml additions
dependencies = [
    # ... existing ...
    "websockets>=12.0",
]

# Optional: SSE alternative
# "sse-starlette>=1.8",
```

---

## Conclusion

This design provides a robust WebSocket integration for the Harness Lab TUI:

1. **Real-time updates**: WebSocket pushes events instantly
2. **Graceful fallback**: HTTP polling when WebSocket unavailable
3. **Clean architecture**: Worker + Message pattern in Textual
4. **Dual transport**: HTTP for operations, WebSocket for events
5. **Automatic recovery**: Exponential backoff reconnect

The implementation maintains compatibility with existing `api_client.py` while adding WebSocket capabilities through the EnhancedAPIClient wrapper.