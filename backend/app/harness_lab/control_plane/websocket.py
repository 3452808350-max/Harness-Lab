"""
Control Plane WebSocket Router.

Provides real-time event streaming via WebSocket for TUI dashboard.

Features:
- WebSocketConnectionManager for connection lifecycle management
- WebSocketEventPublisher for clean event publishing interface
- 4 dedicated endpoints: /ws/workers, /ws/queues, /ws/leases, /ws/health
- Heartbeat detection (30s interval, 90s timeout)
- Filter-based routing (worker_id, lease_id, run_id)
- Initial state snapshot on connect

Message Envelope Format:
{
    "event_type": "<event_name>",
    "timestamp": <unix_timestamp_ms>,
    "sequence": <sequence_number>,
    "data": {...},
    "filters": {
        "worker_id": "...",  # optional
        "lease_id": "...",   # optional
        "run_id": "..."      # optional
    }
}
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Callable
from dataclasses import dataclass, field
from enum import Enum

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..bootstrap import harness_lab_services


router = APIRouter(prefix="/ws", tags=["websocket"])


# === Constants ===

HEARTBEAT_INTERVAL_SECONDS = 30  # Send heartbeat every 30s
HEARTBEAT_TIMEOUT_SECONDS = 90   # Disconnect if no response for 90s
MAX_CONNECTIONS = 100            # Maximum concurrent connections
RECONNECT_GRACE_PERIOD = 5       # Grace period for reconnect state sync


class EventType(str, Enum):
    """WebSocket event types."""
    # Connection events
    CONNECTION_ACK = "connection_ack"
    INITIAL_STATE = "initial_state"
    HEARTBEAT = "heartbeat"
    HEARTBEAT_ACK = "heartbeat_ack"
    PONG = "pong"
    SUBSCRIBED = "subscribed"
    UNSUBSCRIBED = "unsubscribed"
    ERROR = "error"
    
    # Worker events
    WORKER_JOINED = "worker_joined"
    WORKER_LEFT = "worker_left"
    WORKER_STATE_CHANGED = "worker_state_changed"
    WORKER_DRAIN = "worker_drain"
    WORKER_RESUME = "worker_resume"
    WORKER_HEARTBEAT = "worker_heartbeat"
    
    # Task/Lease events
    TASK_DISPATCHED = "task_dispatched"
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    LEASE_CREATED = "lease_created"
    LEASE_RELEASED = "lease_released"
    LEASE_EXPIRED = "lease_expired"
    LEASE_HEARTBEAT = "lease_heartbeat"
    
    # Queue events
    QUEUE_UPDATE = "queue_update"
    QUEUE_SHARD_UPDATE = "queue_shard_update"
    QUEUE_DEPTH_CHANGE = "queue_depth_change"
    
    # Health events
    HEALTH_UPDATE = "health_update"
    SYSTEM_HEALTH = "system_health"
    COMPONENT_HEALTH = "component_health"
    DOCTOR_REPORT = "doctor_report"
    
    # Run events
    RUN_CREATED = "run_created"
    RUN_STARTED = "run_started"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    RUN_STUCK = "run_stuck"
    
    # System events
    SYSTEM_EVENT = "system_event"
    POLICY_VERDICT = "policy_verdict"
    APPROVAL_REQUEST = "approval_request"
    APPROVAL_DECISION = "approval_decision"


@dataclass
class ConnectionFilters:
    """Filter configuration for a WebSocket connection."""
    worker_ids: Set[str] = field(default_factory=set)
    lease_ids: Set[str] = field(default_factory=set)
    run_ids: Set[str] = field(default_factory=set)
    event_types: Set[str] = field(default_factory=set)
    
    def matches(self, event_type: str, data: Dict[str, Any]) -> bool:
        """Check if event matches this connection's filters."""
        # Empty filters means subscribe to all
        if not self.worker_ids and not self.lease_ids and not self.run_ids and not self.event_types:
            return True
        
        # Check event type filter
        if self.event_types and event_type not in self.event_types:
            return False
        
        # Check entity filters
        worker_id = data.get("worker_id")
        lease_id = data.get("lease_id")
        run_id = data.get("run_id")
        
        # If any filter is set, at least one must match
        if self.worker_ids or self.lease_ids or self.run_ids:
            matched = False
            if worker_id and worker_id in self.worker_ids:
                matched = True
            if lease_id and lease_id in self.lease_ids:
                matched = True
            if run_id and run_id in self.run_ids:
                matched = True
            if not matched:
                return False
        
        return True


@dataclass
class ConnectionInfo:
    """Metadata for a WebSocket connection."""
    connection_id: str
    websocket: WebSocket
    filters: ConnectionFilters
    connected_at: float
    last_heartbeat_sent: float = 0.0
    last_heartbeat_received: float = 0.0
    last_message_received: float = 0.0
    sequence_number: int = 0
    endpoint_type: str = "general"  # workers, queues, leases, health, general
    is_alive: bool = True


class WebSocketConnectionManager:
    """Manage WebSocket connections with filtering and heartbeat.
    
    Features:
    - Connection registration/removal with metadata tracking
    - Filter-based routing (worker_id, lease_id, run_id, event_type)
    - Heartbeat management (30s interval, 90s timeout)
    - Broadcast with automatic filtering
    - Connection health tracking
    - Sequence numbers for message ordering
    
    Design source: Mission Control WebSocket Architecture
    """
    
    def __init__(self):
        self._connections: Dict[WebSocket, ConnectionInfo] = {}
        self._connection_counter: int = 0
        self._lock = asyncio.Lock()
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._cleanup_task: Optional[asyncio.Task] = None
        self._sequence_counter: int = 0
        self._is_initialized: bool = False
    
    async def initialize(self) -> None:
        """Initialize the connection manager and start background tasks."""
        if self._is_initialized:
            return
        
        self._is_initialized = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
    
    async def shutdown(self) -> None:
        """Shutdown the connection manager."""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        
        # Close all connections
        async with self._lock:
            for conn_info in self._connections.values():
                try:
                    await conn_info.websocket.close(code=1001, reason="Server shutdown")
                except Exception:
                    pass
            self._connections.clear()
        
        self._is_initialized = False
    
    async def connect(
        self,
        websocket: WebSocket,
        endpoint_type: str = "general",
        initial_filters: Optional[Dict[str, List[str]]] = None,
    ) -> str:
        """Accept and register new connection.
        
        Args:
            websocket: WebSocket connection
            endpoint_type: Type of endpoint (workers, queues, leases, health, general)
            initial_filters: Optional initial filter configuration
            
        Returns:
            Connection ID
        """
        await websocket.accept()
        
        async with self._lock:
            # Check connection limit
            if len(self._connections) >= MAX_CONNECTIONS:
                await websocket.close(code=1013, reason="Connection limit reached")
                raise ValueError("Maximum connections reached")
            
            self._connection_counter += 1
            conn_id = f"ws-{endpoint_type}-{self._connection_counter}"
            
            # Parse initial filters
            filters = ConnectionFilters()
            if initial_filters:
                filters.worker_ids = set(initial_filters.get("worker_ids", []))
                filters.lease_ids = set(initial_filters.get("lease_ids", []))
                filters.run_ids = set(initial_filters.get("run_ids", []))
                filters.event_types = set(initial_filters.get("event_types", []))
            
            now = time.time()
            conn_info = ConnectionInfo(
                connection_id=conn_id,
                websocket=websocket,
                filters=filters,
                connected_at=now,
                last_heartbeat_sent=now,
                last_heartbeat_received=now,
                last_message_received=now,
                endpoint_type=endpoint_type,
            )
            
            self._connections[websocket] = conn_info
        
        return conn_id
    
    async def disconnect(self, websocket: WebSocket) -> None:
        """Remove connection."""
        async with self._lock:
            if websocket in self._connections:
                conn_info = self._connections.pop(websocket)
                conn_info.is_alive = False
    
    async def update_filters(
        self,
        websocket: WebSocket,
        filters_update: Dict[str, Any],
    ) -> None:
        """Update connection filters.
        
        Args:
            websocket: WebSocket connection
            filters_update: Filter update dict with add/remove operations
        """
        async with self._lock:
            if websocket not in self._connections:
                return
            
            conn_info = self._connections[websocket]
            
            # Handle filter updates
            action = filters_update.get("action", "set")
            
            if action == "set":
                # Replace all filters
                conn_info.filters.worker_ids = set(filters_update.get("worker_ids", []))
                conn_info.filters.lease_ids = set(filters_update.get("lease_ids", []))
                conn_info.filters.run_ids = set(filters_update.get("run_ids", []))
                conn_info.filters.event_types = set(filters_update.get("event_types", []))
            
            elif action == "add":
                # Add to existing filters
                conn_info.filters.worker_ids.update(filters_update.get("worker_ids", []))
                conn_info.filters.lease_ids.update(filters_update.get("lease_ids", []))
                conn_info.filters.run_ids.update(filters_update.get("run_ids", []))
                conn_info.filters.event_types.update(filters_update.get("event_types", []))
            
            elif action == "remove":
                # Remove from filters
                for wid in filters_update.get("worker_ids", []):
                    conn_info.filters.worker_ids.discard(wid)
                for lid in filters_update.get("lease_ids", []):
                    conn_info.filters.lease_ids.discard(lid)
                for rid in filters_update.get("run_ids", []):
                    conn_info.filters.run_ids.discard(rid)
                for et in filters_update.get("event_types", []):
                    conn_info.filters.event_types.discard(et)
            
            elif action == "clear":
                # Clear all filters (subscribe to all)
                conn_info.filters = ConnectionFilters()
    
    def _should_send(
        self,
        conn_info: ConnectionInfo,
        event_type: str,
        data: Dict[str, Any],
    ) -> bool:
        """Check if connection should receive event."""
        if not conn_info.is_alive:
            return False
        
        return conn_info.filters.matches(event_type, data)
    
    async def _create_envelope(
        self,
        event_type: str,
        data: Dict[str, Any],
        filters: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Create message envelope."""
        async with self._lock:
            self._sequence_counter += 1
            seq = self._sequence_counter
        
        return {
            "event_type": event_type,
            "timestamp": int(time.time() * 1000),
            "sequence": seq,
            "data": data,
            "filters": filters or {},
        }
    
    async def broadcast(
        self,
        event_type: str,
        data: Dict[str, Any],
        filters: Optional[Dict[str, str]] = None,
    ) -> int:
        """Broadcast message to matching connections.
        
        Args:
            event_type: Event type
            data: Event data
            filters: Optional filter hints for routing
        
        Returns:
            Number of connections that received message
        """
        envelope = await self._create_envelope(event_type, data, filters)
        sent_count = 0
        
        async with self._lock:
            disconnected = []
            
            for ws, conn_info in self._connections.items():
                if not self._should_send(conn_info, event_type, data):
                    continue
                
                try:
                    await ws.send_json(envelope)
                    conn_info.sequence_number = envelope["sequence"]
                    sent_count += 1
                except Exception:
                    conn_info.is_alive = False
                    disconnected.append(ws)
            
            # Clean up disconnected
            for ws in disconnected:
                self._connections.pop(ws, None)
        
        return sent_count
    
    async def send_to(
        self,
        websocket: WebSocket,
        event_type: str,
        data: Dict[str, Any],
    ) -> bool:
        """Send message to specific connection.
        
        Args:
            websocket: Target WebSocket
            event_type: Event type
            data: Event data
            
        Returns:
            True if sent successfully
        """
        envelope = await self._create_envelope(event_type, data)
        
        async with self._lock:
            if websocket not in self._connections:
                return False
            
            conn_info = self._connections[websocket]
            
            try:
                await websocket.send_json(envelope)
                conn_info.sequence_number = envelope["sequence"]
                return True
            except Exception:
                conn_info.is_alive = False
                self._connections.pop(websocket, None)
                return False
    
    async def record_heartbeat_received(self, websocket: WebSocket) -> None:
        """Record that client responded to heartbeat."""
        async with self._lock:
            if websocket in self._connections:
                self._connections[websocket].last_heartbeat_received = time.time()
    
    async def record_message_received(self, websocket: WebSocket) -> None:
        """Record that client sent a message."""
        async with self._lock:
            if websocket in self._connections:
                self._connections[websocket].last_message_received = time.time()
    
    async def _heartbeat_loop(self) -> None:
        """Background task to send heartbeats."""
        while True:
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
                
                async with self._lock:
                    now = time.time()
                    
                    for ws, conn_info in self._connections.items():
                        # Check for timeout
                        time_since_last = now - conn_info.last_heartbeat_received
                        
                        if time_since_last > HEARTBEAT_TIMEOUT_SECONDS:
                            # Connection timed out
                            conn_info.is_alive = False
                            try:
                                await ws.close(code=1001, reason="Heartbeat timeout")
                            except Exception:
                                pass
                            continue
                        
                        # Send heartbeat
                        try:
                            envelope = await self._create_envelope(
                                EventType.HEARTBEAT,
                                {"connection_count": len(self._connections)}
                            )
                            await ws.send_json(envelope)
                            conn_info.last_heartbeat_sent = now
                        except Exception:
                            conn_info.is_alive = False
                    
                    # Remove dead connections
                    dead = [ws for ws, info in self._connections.items() if not info.is_alive]
                    for ws in dead:
                        self._connections.pop(ws, None)
                        
            except asyncio.CancelledError:
                break
            except Exception:
                # Log and continue
                await asyncio.sleep(1)
    
    async def _cleanup_loop(self) -> None:
        """Background task to clean up stale connections."""
        while True:
            try:
                await asyncio.sleep(60)  # Check every minute
                
                async with self._lock:
                    now = time.time()
                    stale = []
                    
                    for ws, conn_info in self._connections.items():
                        # Check if connection is stale (no activity for 2x timeout)
                        if now - conn_info.last_message_received > HEARTBEAT_TIMEOUT_SECONDS * 2:
                            stale.append(ws)
                            conn_info.is_alive = False
                    
                    for ws in stale:
                        try:
                            await ws.close(code=1001, reason="Connection stale")
                        except Exception:
                            pass
                        self._connections.pop(ws, None)
                        
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(1)
    
    @property
    def connection_count(self) -> int:
        """Get number of active connections."""
        return len(self._connections)
    
    def get_connection_info(self) -> List[Dict[str, Any]]:
        """Get list of connection info."""
        return [
            {
                "connection_id": info.connection_id,
                "endpoint_type": info.endpoint_type,
                "connected_at": info.connected_at,
                "sequence": info.sequence_number,
                "filters": {
                    "worker_ids": list(info.filters.worker_ids),
                    "lease_ids": list(info.filters.lease_ids),
                    "run_ids": list(info.filters.run_ids),
                    "event_types": list(info.filters.event_types),
                },
            }
            for info in self._connections.values()
        ]


class WebSocketEventPublisher:
    """Publisher for WebSocket events.
    
    Provides a clean interface for the runtime to publish events
    to WebSocket clients.
    
    Design source: Mission Control Event Publisher Pattern
    """
    
    def __init__(self, manager: WebSocketConnectionManager):
        self._manager = manager
    
    async def publish_worker_event(
        self,
        event_type: str,
        worker_id: str,
        **extra: Any,
    ) -> int:
        """Publish worker-related event.
        
        Args:
            event_type: Event type (worker_joined, worker_left, etc.)
            worker_id: Worker identifier
            **extra: Additional event data
            
        Returns:
            Number of recipients
        """
        data = {"worker_id": worker_id, **extra}
        filters = {"worker_id": worker_id}
        return await self._manager.broadcast(event_type, data, filters)
    
    async def publish_lease_event(
        self,
        event_type: str,
        lease_id: str,
        worker_id: Optional[str] = None,
        run_id: Optional[str] = None,
        **extra: Any,
    ) -> int:
        """Publish lease-related event.
        
        Args:
            event_type: Event type
            lease_id: Lease identifier
            worker_id: Optional worker ID
            run_id: Optional run ID
            **extra: Additional event data
            
        Returns:
            Number of recipients
        """
        data = {"lease_id": lease_id, **extra}
        if worker_id:
            data["worker_id"] = worker_id
        if run_id:
            data["run_id"] = run_id
        
        filters = {"lease_id": lease_id}
        return await self._manager.broadcast(event_type, data, filters)
    
    async def publish_queue_event(
        self,
        event_type: str,
        queue_data: Dict[str, Any],
    ) -> int:
        """Publish queue-related event.
        
        Args:
            event_type: Event type
            queue_data: Queue status data
            
        Returns:
            Number of recipients
        """
        return await self._manager.broadcast(event_type, queue_data)
    
    async def publish_health_event(
        self,
        event_type: str,
        health_data: Dict[str, Any],
    ) -> int:
        """Publish health-related event.
        
        Args:
            event_type: Event type
            health_data: Health status data
            
        Returns:
            Number of recipients
        """
        return await self._manager.broadcast(event_type, health_data)
    
    async def publish_run_event(
        self,
        event_type: str,
        run_id: str,
        **extra: Any,
    ) -> int:
        """Publish run-related event.
        
        Args:
            event_type: Event type
            run_id: Run identifier
            **extra: Additional event data
            
        Returns:
            Number of recipients
        """
        data = {"run_id": run_id, **extra}
        filters = {"run_id": run_id}
        return await self._manager.broadcast(event_type, data, filters)
    
    async def publish_system_event(
        self,
        event_name: str,
        message: str,
        level: str = "info",
        details: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Publish generic system event.
        
        Args:
            event_name: Event name
            message: Human-readable message
            level: Event level (info, warning, error)
            details: Additional details
            
        Returns:
            Number of recipients
        """
        data = {
            "event_name": event_name,
            "message": message,
            "level": level,
            "details": details or {},
        }
        return await self._manager.broadcast(EventType.SYSTEM_EVENT, data)


# === Global instances ===

_manager: Optional[WebSocketConnectionManager] = None
_publisher: Optional[WebSocketEventPublisher] = None


def get_manager() -> WebSocketConnectionManager:
    """Get the global WebSocket connection manager."""
    global _manager
    if _manager is None:
        _manager = WebSocketConnectionManager()
    return _manager


def get_publisher() -> WebSocketEventPublisher:
    """Get the global WebSocket event publisher."""
    global _publisher
    if _publisher is None:
        _publisher = WebSocketEventPublisher(get_manager())
    return _publisher


async def initialize_websocket_support() -> None:
    """Initialize WebSocket support (called from lifespan)."""
    manager = get_manager()
    await manager.initialize()


async def shutdown_websocket_support() -> None:
    """Shutdown WebSocket support."""
    manager = get_manager()
    await manager.shutdown()


# === Helper Functions ===

async def get_worker_snapshot() -> Dict[str, Any]:
    """Get current worker state snapshot."""
    try:
        workers = harness_lab_services.workers.list_workers()
        return {
            "workers": [worker.model_dump() for worker in workers],
            "count": len(workers),
            "by_state": {},
            "by_role": {},
        }
    except Exception:
        return {"workers": [], "count": 0, "by_state": {}, "by_role": {}}


async def get_queue_snapshot() -> Dict[str, Any]:
    """Get current queue state snapshot."""
    try:
        execution = harness_lab_services.runtime.execution_plane_status()
        return {
            "queue_status": execution.get("queue_status", []),
            "pending_tasks": execution.get("pending_tasks", 0),
            "active_leases": execution.get("active_leases", 0),
        }
    except Exception:
        return {"queue_status": [], "pending_tasks": 0, "active_leases": 0}


async def get_lease_snapshot() -> Dict[str, Any]:
    """Get current lease state snapshot."""
    try:
        leases = harness_lab_services.runtime.list_leases()
        return {
            "leases": [lease.model_dump() for lease in leases],
            "count": len(leases),
            "active": len([l for l in leases if l.status == "active"]),
            "pending": len([l for l in leases if l.status == "pending"]),
        }
    except Exception:
        return {"leases": [], "count": 0, "active": 0, "pending": 0}


async def get_health_snapshot() -> Dict[str, Any]:
    """Get current health state snapshot."""
    try:
        execution = harness_lab_services.runtime.execution_plane_status()
        return {
            "postgres_ready": execution.get("postgres_ready", False),
            "redis_ready": execution.get("redis_ready", False),
            "docker_ready": execution.get("docker_ready", False),
            "healthy_workers": execution.get("healthy_workers", []),
            "unhealthy_workers": execution.get("unhealthy_workers", []),
            "draining_workers": execution.get("draining_workers", []),
            "stuck_runs": len(execution.get("stuck_runs", [])),
        }
    except Exception:
        return {
            "postgres_ready": False,
            "redis_ready": False,
            "docker_ready": False,
            "healthy_workers": [],
            "unhealthy_workers": [],
            "draining_workers": [],
            "stuck_runs": 0,
        }


async def handle_client_command(
    websocket: WebSocket,
    command: str,
    data: Dict[str, Any],
) -> None:
    """Handle client command messages.
    
    Args:
        websocket: WebSocket connection
        command: Command name
        data: Full message data
    """
    manager = get_manager()
    
    if command == "ping":
        await manager.send_to(websocket, EventType.PONG, {})
    
    elif command == "heartbeat_ack":
        await manager.record_heartbeat_received(websocket)
    
    elif command == "subscribe":
        await manager.update_filters(websocket, {"action": "add", **data})
        await manager.send_to(websocket, EventType.SUBSCRIBED, {
            "filters": data,
        })
    
    elif command == "unsubscribe":
        await manager.update_filters(websocket, {"action": "remove", **data})
        await manager.send_to(websocket, EventType.UNSUBSCRIBED, {
            "filters": data,
        })
    
    elif command == "set_filters":
        await manager.update_filters(websocket, {"action": "set", **data})
        await manager.send_to(websocket, EventType.SUBSCRIBED, {
            "filters": data,
        })
    
    elif command == "clear_filters":
        await manager.update_filters(websocket, {"action": "clear"})
        await manager.send_to(websocket, EventType.SUBSCRIBED, {
            "filters": {},
            "message": "Subscribed to all events",
        })
    
    elif command == "get_snapshot":
        # Send current snapshot based on endpoint type
        conn_info = manager._connections.get(websocket)
        if conn_info:
            endpoint_type = conn_info.endpoint_type
            if endpoint_type == "workers":
                snapshot = await get_worker_snapshot()
            elif endpoint_type == "queues":
                snapshot = await get_queue_snapshot()
            elif endpoint_type == "leases":
                snapshot = await get_lease_snapshot()
            elif endpoint_type == "health":
                snapshot = await get_health_snapshot()
            else:
                snapshot = {
                    "workers": await get_worker_snapshot(),
                    "queues": await get_queue_snapshot(),
                    "leases": await get_lease_snapshot(),
                    "health": await get_health_snapshot(),
                }
            await manager.send_to(websocket, EventType.INITIAL_STATE, snapshot)
    
    else:
        await manager.send_to(websocket, EventType.ERROR, {
            "message": f"Unknown command: {command}",
        })


# === WebSocket Endpoints ===

@router.websocket("/workers")
async def workers_websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for worker events.
    
    Provides:
    - Initial worker snapshot
    - Worker join/leave events
    - Worker state changes
    - Worker drain/resume events
    
    Client Commands:
    - {"command": "ping"} → pong
    - {"command": "subscribe", "worker_ids": [...]} → Filter to specific workers
    - {"command": "clear_filters"} → Subscribe to all workers
    - {"command": "get_snapshot"} → Get current worker state
    """
    manager = get_manager()
    conn_id = await manager.connect(websocket, endpoint_type="workers")
    
    try:
        # Send connection acknowledgment
        await manager.send_to(websocket, EventType.CONNECTION_ACK, {
            "connection_id": conn_id,
            "endpoint": "workers",
            "server_time": datetime.now(timezone.utc).isoformat(),
        })
        
        # Send initial worker snapshot
        snapshot = await get_worker_snapshot()
        await manager.send_to(websocket, EventType.INITIAL_STATE, snapshot)
        
        # Main message loop
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_json(),
                    timeout=HEARTBEAT_INTERVAL_SECONDS
                )
                
                await manager.record_message_received(websocket)
                command = data.get("command")
                
                if command:
                    await handle_client_command(websocket, command, data)
                elif data.get("event_type") == EventType.HEARTBEAT_ACK:
                    await manager.record_heartbeat_received(websocket)
                    
            except asyncio.TimeoutError:
                # Heartbeat will be sent by background task
                continue
                
            except json.JSONDecodeError:
                await manager.send_to(websocket, EventType.ERROR, {
                    "message": "Invalid JSON",
                })
                continue
                
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
        
    except Exception as e:
        await manager.disconnect(websocket)


@router.websocket("/queues")
async def queues_websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for queue events.
    
    Provides:
    - Initial queue snapshot
    - Queue depth changes
    - Queue shard updates
    - Pending task counts
    
    Client Commands:
    - {"command": "ping"} → pong
    - {"command": "get_snapshot"} → Get current queue state
    """
    manager = get_manager()
    conn_id = await manager.connect(websocket, endpoint_type="queues")
    
    try:
        # Send connection acknowledgment
        await manager.send_to(websocket, EventType.CONNECTION_ACK, {
            "connection_id": conn_id,
            "endpoint": "queues",
            "server_time": datetime.now(timezone.utc).isoformat(),
        })
        
        # Send initial queue snapshot
        snapshot = await get_queue_snapshot()
        await manager.send_to(websocket, EventType.INITIAL_STATE, snapshot)
        
        # Main message loop
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_json(),
                    timeout=HEARTBEAT_INTERVAL_SECONDS
                )
                
                await manager.record_message_received(websocket)
                command = data.get("command")
                
                if command:
                    await handle_client_command(websocket, command, data)
                elif data.get("event_type") == EventType.HEARTBEAT_ACK:
                    await manager.record_heartbeat_received(websocket)
                    
            except asyncio.TimeoutError:
                continue
                
            except json.JSONDecodeError:
                await manager.send_to(websocket, EventType.ERROR, {
                    "message": "Invalid JSON",
                })
                continue
                
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
        
    except Exception:
        await manager.disconnect(websocket)


@router.websocket("/leases")
async def leases_websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for lease events.
    
    Provides:
    - Initial lease snapshot
    - Lease created/released/expired events
    - Lease heartbeat events
    
    Client Commands:
    - {"command": "ping"} → pong
    - {"command": "subscribe", "lease_ids": [...]} → Filter to specific leases
    - {"command": "subscribe", "run_ids": [...]} → Filter to specific runs
    - {"command": "clear_filters"} → Subscribe to all leases
    - {"command": "get_snapshot"} → Get current lease state
    """
    manager = get_manager()
    conn_id = await manager.connect(websocket, endpoint_type="leases")
    
    try:
        # Send connection acknowledgment
        await manager.send_to(websocket, EventType.CONNECTION_ACK, {
            "connection_id": conn_id,
            "endpoint": "leases",
            "server_time": datetime.now(timezone.utc).isoformat(),
        })
        
        # Send initial lease snapshot
        snapshot = await get_lease_snapshot()
        await manager.send_to(websocket, EventType.INITIAL_STATE, snapshot)
        
        # Main message loop
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_json(),
                    timeout=HEARTBEAT_INTERVAL_SECONDS
                )
                
                await manager.record_message_received(websocket)
                command = data.get("command")
                
                if command:
                    await handle_client_command(websocket, command, data)
                elif data.get("event_type") == EventType.HEARTBEAT_ACK:
                    await manager.record_heartbeat_received(websocket)
                    
            except asyncio.TimeoutError:
                continue
                
            except json.JSONDecodeError:
                await manager.send_to(websocket, EventType.ERROR, {
                    "message": "Invalid JSON",
                })
                continue
                
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
        
    except Exception:
        await manager.disconnect(websocket)


@router.websocket("/health")
async def health_websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for health events.
    
    Provides:
    - Initial health snapshot
    - Health status updates
    - System health events
    - Doctor report updates
    
    Client Commands:
    - {"command": "ping"} → pong
    - {"command": "get_snapshot"} → Get current health state
    - {"command": "get_doctor_report"} → Get full doctor report
    """
    manager = get_manager()
    conn_id = await manager.connect(websocket, endpoint_type="health")
    
    try:
        # Send connection acknowledgment
        await manager.send_to(websocket, EventType.CONNECTION_ACK, {
            "connection_id": conn_id,
            "endpoint": "health",
            "server_time": datetime.now(timezone.utc).isoformat(),
        })
        
        # Send initial health snapshot
        snapshot = await get_health_snapshot()
        await manager.send_to(websocket, EventType.INITIAL_STATE, snapshot)
        
        # Main message loop
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_json(),
                    timeout=HEARTBEAT_INTERVAL_SECONDS
                )
                
                await manager.record_message_received(websocket)
                command = data.get("command")
                
                if command == "get_doctor_report":
                    # Send full doctor report
                    try:
                        report = harness_lab_services.doctor_report()
                        await manager.send_to(websocket, EventType.DOCTOR_REPORT, report)
                    except Exception as e:
                        await manager.send_to(websocket, EventType.ERROR, {
                            "message": f"Failed to get doctor report: {str(e)}",
                        })
                elif command:
                    await handle_client_command(websocket, command, data)
                elif data.get("event_type") == EventType.HEARTBEAT_ACK:
                    await manager.record_heartbeat_received(websocket)
                    
            except asyncio.TimeoutError:
                continue
                
            except json.JSONDecodeError:
                await manager.send_to(websocket, EventType.ERROR, {
                    "message": "Invalid JSON",
                })
                continue
                
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
        
    except Exception:
        await manager.disconnect(websocket)


@router.websocket("/")
async def general_websocket_endpoint(websocket: WebSocket):
    """General WebSocket endpoint for all events.
    
    Provides:
    - Initial full snapshot (workers, queues, leases, health)
    - All event types with optional filtering
    
    Client Commands:
    - {"command": "ping"} → pong
    - {"command": "subscribe", "worker_ids": [...]} → Filter workers
    - {"command": "subscribe", "lease_ids": [...]} → Filter leases
    - {"command": "subscribe", "run_ids": [...]} → Filter runs
    - {"command": "subscribe", "event_types": [...]} → Filter event types
    - {"command": "clear_filters"} → Subscribe to all
    - {"command": "get_snapshot"} → Get full snapshot
    """
    manager = get_manager()
    conn_id = await manager.connect(websocket, endpoint_type="general")
    
    try:
        # Send connection acknowledgment
        await manager.send_to(websocket, EventType.CONNECTION_ACK, {
            "connection_id": conn_id,
            "endpoint": "general",
            "server_time": datetime.now(timezone.utc).isoformat(),
        })
        
        # Send initial full snapshot
        snapshot = {
            "workers": await get_worker_snapshot(),
            "queues": await get_queue_snapshot(),
            "leases": await get_lease_snapshot(),
            "health": await get_health_snapshot(),
        }
        await manager.send_to(websocket, EventType.INITIAL_STATE, snapshot)
        
        # Main message loop
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_json(),
                    timeout=HEARTBEAT_INTERVAL_SECONDS
                )
                
                await manager.record_message_received(websocket)
                command = data.get("command")
                
                if command:
                    await handle_client_command(websocket, command, data)
                elif data.get("event_type") == EventType.HEARTBEAT_ACK:
                    await manager.record_heartbeat_received(websocket)
                    
            except asyncio.TimeoutError:
                continue
                
            except json.JSONDecodeError:
                await manager.send_to(websocket, EventType.ERROR, {
                    "message": "Invalid JSON",
                })
                continue
                
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
        
    except Exception:
        await manager.disconnect(websocket)


# === HTTP Status Endpoint ===

@router.get("/status")
async def websocket_status() -> Dict[str, Any]:
    """Get WebSocket connection status.
    
    Returns:
        Status dict with connection count and details
    """
    manager = get_manager()
    return {
        "success": True,
        "data": {
            "active_connections": manager.connection_count,
            "connections": manager.get_connection_info(),
            "heartbeat_interval": HEARTBEAT_INTERVAL_SECONDS,
            "heartbeat_timeout": HEARTBEAT_TIMEOUT_SECONDS,
            "max_connections": MAX_CONNECTIONS,
        }
    }


# === Event Broadcasting Functions ===
# These are called by Control Plane when events occur

async def broadcast_worker_joined(
    worker_id: str,
    role: str,
    hostname: Optional[str] = None,
    pid: Optional[int] = None,
) -> None:
    """Broadcast worker join event."""
    publisher = get_publisher()
    await publisher.publish_worker_event(
        EventType.WORKER_JOINED,
        worker_id,
        role=role,
        state="idle",
        hostname=hostname,
        pid=pid,
    )


async def broadcast_worker_left(worker_id: str) -> None:
    """Broadcast worker leave event."""
    publisher = get_publisher()
    await publisher.publish_worker_event(
        EventType.WORKER_LEFT,
        worker_id,
    )


async def broadcast_worker_state_change(
    worker_id: str,
    old_state: str,
    new_state: str,
    task_id: Optional[str] = None,
    lease_id: Optional[str] = None,
) -> None:
    """Broadcast worker state change event."""
    publisher = get_publisher()
    await publisher.publish_worker_event(
        EventType.WORKER_STATE_CHANGED,
        worker_id,
        old_state=old_state,
        new_state=new_state,
        task_id=task_id,
        lease_id=lease_id,
    )


async def broadcast_worker_drain(
    worker_id: str,
    reason: Optional[str] = None,
    initiator: Optional[str] = None,
) -> None:
    """Broadcast worker drain event."""
    publisher = get_publisher()
    await publisher.publish_worker_event(
        EventType.WORKER_DRAIN,
        worker_id,
        reason=reason,
        initiator=initiator,
    )


async def broadcast_worker_resume(worker_id: str) -> None:
    """Broadcast worker resume event."""
    publisher = get_publisher()
    await publisher.publish_worker_event(
        EventType.WORKER_RESUME,
        worker_id,
    )


async def broadcast_task_dispatched(
    task_id: str,
    worker_id: str,
    lease_id: Optional[str] = None,
    task_type: Optional[str] = None,
    run_id: Optional[str] = None,
) -> None:
    """Broadcast task dispatch event."""
    publisher = get_publisher()
    await publisher.publish_worker_event(
        EventType.TASK_DISPATCHED,
        worker_id,
        task_id=task_id,
        lease_id=lease_id,
        task_type=task_type,
        run_id=run_id,
    )


async def broadcast_task_completed(
    task_id: str,
    worker_id: str,
    success: bool,
    duration_seconds: Optional[float] = None,
    error: Optional[str] = None,
    lease_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> None:
    """Broadcast task completion event."""
    publisher = get_publisher()
    event_type = EventType.TASK_COMPLETED if success else EventType.TASK_FAILED
    await publisher.publish_worker_event(
        event_type,
        worker_id,
        task_id=task_id,
        success=success,
        duration_seconds=duration_seconds,
        error=error,
        lease_id=lease_id,
        run_id=run_id,
    )


async def broadcast_lease_created(
    lease_id: str,
    worker_id: str,
    run_id: Optional[str] = None,
    lease_type: Optional[str] = None,
) -> None:
    """Broadcast lease created event."""
    publisher = get_publisher()
    await publisher.publish_lease_event(
        EventType.LEASE_CREATED,
        lease_id,
        worker_id=worker_id,
        run_id=run_id,
        lease_type=lease_type,
    )


async def broadcast_lease_released(
    lease_id: str,
    worker_id: Optional[str] = None,
    run_id: Optional[str] = None,
    reason: Optional[str] = None,
) -> None:
    """Broadcast lease released event."""
    publisher = get_publisher()
    await publisher.publish_lease_event(
        EventType.LEASE_RELEASED,
        lease_id,
        worker_id=worker_id,
        run_id=run_id,
        reason=reason,
    )


async def broadcast_lease_expired(
    lease_id: str,
    worker_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> None:
    """Broadcast lease expired event."""
    publisher = get_publisher()
    await publisher.publish_lease_event(
        EventType.LEASE_EXPIRED,
        lease_id,
        worker_id=worker_id,
        run_id=run_id,
    )


async def broadcast_health_update(health_data: Dict[str, Any]) -> None:
    """Broadcast health status update."""
    publisher = get_publisher()
    await publisher.publish_health_event(EventType.HEALTH_UPDATE, health_data)


async def broadcast_queue_update(queues_data: List[Dict[str, Any]]) -> None:
    """Broadcast queue status update."""
    publisher = get_publisher()
    await publisher.publish_queue_event(EventType.QUEUE_UPDATE, {"queues": queues_data})


async def broadcast_run_event(
    event_type: str,
    run_id: str,
    **extra: Any,
) -> None:
    """Broadcast run-related event."""
    publisher = get_publisher()
    await publisher.publish_run_event(event_type, run_id, **extra)


async def broadcast_system_event(
    event_name: str,
    message: str,
    level: str = "info",
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """Broadcast generic system event."""
    publisher = get_publisher()
    await publisher.publish_system_event(event_name, message, level, details)


# Legacy compatibility - old manager reference
manager = None  # Use get_manager() instead