# Harness Lab Control Plane WebSocket API Design

## Overview

This document describes the WebSocket API design for real-time state monitoring in the Harness Lab Control Plane. The WebSocket endpoints complement the existing HTTP REST API by providing push-based updates for Worker status, Queue depth, Lease state transitions, and System health.

## Design Goals

1. **Real-time Updates**: Push state changes immediately without polling
2. **Scalability**: Support multiple concurrent clients with efficient broadcast
3. **Reliability**: Handle connection drops with automatic reconnection support
4. **Integration**: Seamless integration with existing HTTP API and service architecture
5. **Simplicity**: Clear message format with JSON schemas for validation

## Architecture Context

### Current HTTP API Structure

The Control Plane HTTP API (`backend/app/harness_lab/control_plane/`) provides:
- **Workers**: `/workers/*` - Worker registration, heartbeat, polling
- **Leases**: `/leases/*` - Lease lifecycle management
- **System**: `/system/*` - Health checks, doctor reports
- **Runs**: `/runs/*` - Run creation, status queries

### Service Dependencies

WebSocket endpoints will integrate with:
- `RuntimeService` - Execution plane state
- `WorkerRegistry` - Worker pool state
- `LeaseManager` - Lease lifecycle events
- `DispatchQueue` - Queue depth metrics
- `PlatformStore` - Database event source

---

## WebSocket Endpoints

### 1. `/ws/workers` - Worker Status Stream

**Purpose**: Real-time worker state changes (registration, heartbeat, state transitions)

**Connection URL**: `ws://host/ws/workers`

**Query Parameters**:
- `worker_id` (optional): Filter to specific worker
- `role_profile` (optional): Filter by agent role (planner, executor, reviewer, etc.)
- `state_filter` (optional): Filter by state (idle, leased, executing, draining)

**Message Types**:

| Type | Direction | Description |
|------|-----------|-------------|
| `worker_registered` | Server → Client | New worker joined the pool |
| `worker_state_changed` | Server → Client | Worker state transition |
| `worker_heartbeat` | Server → Client | Worker heartbeat received |
| `worker_draining` | Server → Client | Worker entering drain mode |
| `worker_offline` | Server → Client | Worker marked offline/unhealthy |
| `worker_list_snapshot` | Server → Client | Full worker list (on connect or request) |
| `ping` | Bidirectional | Heartbeat keepalive |

**Example Message**:

```json
{
  "type": "worker_state_changed",
  "timestamp": "2026-04-18T00:30:00Z",
  "payload": {
    "worker_id": "worker_abc123",
    "label": "executor-node-1",
    "previous_state": "idle",
    "current_state": "leased",
    "role_profile": "executor",
    "current_run_id": "run_xyz789",
    "current_task_node_id": "task_node_001",
    "lease_count": 1,
    "heartbeat_at": "2026-04-18T00:29:55Z"
  }
}
```

---

### 2. `/ws/queues` - Queue Depth Stream

**Purpose**: Real-time dispatch queue metrics per shard

**Connection URL**: `ws://host/ws/queues`

**Query Parameters**:
- `shard` (optional): Filter to specific queue shard (default: all shards)
- `include_samples` (optional): Include sample task references (default: false)

**Message Types**:

| Type | Direction | Description |
|------|-----------|-------------|
| `queue_depth_update` | Server → Client | Queue depth changed |
| `queue_task_enqueued` | Server → Client | New task added to queue |
| `queue_task_dequeued` | Server → Client | Task dispatched from queue |
| `queue_snapshot` | Server → Client | Full queue state (on connect) |
| `ping` | Bidirectional | Heartbeat keepalive |

**Example Message**:

```json
{
  "type": "queue_depth_update",
  "timestamp": "2026-04-18T00:30:00Z",
  "payload": {
    "shard": "default",
    "depth": 15,
    "previous_depth": 14,
    "change": +1,
    "enqueued_at": "2026-04-18T00:30:00Z",
    "sample_tasks": [
      {"run_id": "run_abc", "task_node_id": "task_001"},
      {"run_id": "run_def", "task_node_id": "task_002"}
    ]
  }
}
```

---

### 3. `/ws/leases` - Lease Status Stream

**Purpose**: Real-time lease lifecycle events (creation, heartbeat, completion, expiration)

**Connection URL**: `ws://host/ws/leases`

**Query Parameters**:
- `run_id` (optional): Filter to specific run
- `worker_id` (optional): Filter to specific worker
- `status_filter` (optional): Filter by status (leased, running, completed, expired)

**Message Types**:

| Type | Direction | Description |
|------|-----------|-------------|
| `lease_created` | Server → Client | New lease granted |
| `lease_heartbeat` | Server → Client | Lease heartbeat received |
| `lease_running` | Server → Client | Lease transitioned to running |
| `lease_completed` | Server → Client | Lease completed successfully |
| `lease_failed` | Server → Client | Lease failed with error |
| `lease_expired` | Server → Client | Lease expired (reclaimed) |
| `lease_released` | Server → Client | Lease manually released |
| `lease_snapshot` | Server → Client | Active leases snapshot |
| `ping` | Bidirectional | Heartbeat keepalive |

**Example Message**:

```json
{
  "type": "lease_created",
  "timestamp": "2026-04-18T00:30:00Z",
  "payload": {
    "lease_id": "lease_abc123",
    "worker_id": "worker_xyz",
    "run_id": "run_001",
    "task_node_id": "task_node_001",
    "attempt_id": "attempt_001",
    "status": "leased",
    "expires_at": "2026-04-18T00:31:00Z",
    "heartbeat_interval_seconds": 10
  }
}
```

---

### 4. `/ws/health` - System Health Stream

**Purpose**: Real-time system health and diagnostic updates

**Connection URL**: `ws://host/ws/health`

**Query Parameters**:
- `components` (optional): Filter components (postgres, redis, sandbox, knowledge, provider)
- `warning_only` (optional): Only push warnings (default: false)

**Message Types**:

| Type | Direction | Description |
|------|-----------|-------------|
| `health_check_result` | Server → Client | Periodic health check result |
| `warning_raised` | Server → Client | New system warning |
| `warning_resolved` | Server → Client | Warning cleared |
| `doctor_report_update` | Server → Client | Doctor report changed |
| `component_status_change` | Server → Client | Component readiness change |
| `health_snapshot` | Server → Client | Full health status (on connect) |
| `ping` | Bidirectional | Heartbeat keepalive |

**Example Message**:

```json
{
  "type": "warning_raised",
  "timestamp": "2026-04-18T00:30:00Z",
  "payload": {
    "warning_id": "warn_001",
    "component": "postgres",
    "severity": "high",
    "message": "Postgres truth source is not reachable",
    "details": {
      "last_ping_success": "2026-04-18T00:25:00Z",
      "ping_failure_count": 3
    },
    "suggested_action": "Check database connection settings"
  }
}
```

---

## Message Format JSON Schema

### Base Envelope Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "https://harness.lab/schemas/ws-envelope.json",
  "title": "WebSocketMessageEnvelope",
  "type": "object",
  "required": ["type", "timestamp", "payload"],
  "properties": {
    "type": {
      "type": "string",
      "enum": [
        "worker_registered",
        "worker_state_changed",
        "worker_heartbeat",
        "worker_draining",
        "worker_offline",
        "worker_list_snapshot",
        "queue_depth_update",
        "queue_task_enqueued",
        "queue_task_dequeued",
        "queue_snapshot",
        "lease_created",
        "lease_heartbeat",
        "lease_running",
        "lease_completed",
        "lease_failed",
        "lease_expired",
        "lease_released",
        "lease_snapshot",
        "health_check_result",
        "warning_raised",
        "warning_resolved",
        "doctor_report_update",
        "component_status_change",
        "health_snapshot",
        "ping",
        "pong",
        "error",
        "subscribe",
        "unsubscribe"
      ],
      "description": "Message type identifier"
    },
    "timestamp": {
      "type": "string",
      "format": "date-time",
      "description": "ISO 8601 timestamp of event"
    },
    "payload": {
      "type": "object",
      "description": "Event-specific payload"
    },
    "metadata": {
      "type": "object",
      "properties": {
        "correlation_id": {"type": "string"},
        "source": {"type": "string"},
        "version": {"type": "string", "default": "v1"}
      }
    }
  }
}
```

### Worker Payload Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "https://harness.lab/schemas/ws-worker-payload.json",
  "title": "WorkerEventPayload",
  "type": "object",
  "properties": {
    "worker_id": {"type": "string"},
    "label": {"type": "string"},
    "state": {
      "type": "string",
      "enum": ["registering", "idle", "leased", "executing", "draining", "offline", "unhealthy"]
    },
    "previous_state": {
      "type": "string",
      "enum": ["registering", "idle", "leased", "executing", "draining", "offline", "unhealthy"]
    },
    "current_state": {
      "type": "string",
      "enum": ["registering", "idle", "leased", "executing", "draining", "offline", "unhealthy"]
    },
    "role_profile": {
      "type": "string",
      "enum": ["planner", "researcher", "executor", "reviewer", "recovery"]
    },
    "capabilities": {"type": "array", "items": {"type": "string"}},
    "hostname": {"type": "string"},
    "pid": {"type": "integer"},
    "labels": {"type": "array", "items": {"type": "string"}},
    "current_run_id": {"type": "string"},
    "current_task_node_id": {"type": "string"},
    "current_lease_id": {"type": "string"},
    "lease_count": {"type": "integer", "minimum": 0},
    "heartbeat_at": {"type": "string", "format": "date-time"},
    "drain_state": {"type": "string", "enum": ["active", "draining"]},
    "sandbox_backend": {"type": "string"},
    "sandbox_ready": {"type": "boolean"},
    "last_error": {"type": "string"}
  }
}
```

### Lease Payload Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "https://harness.lab/schemas/ws-lease-payload.json",
  "title": "LeaseEventPayload",
  "type": "object",
  "properties": {
    "lease_id": {"type": "string"},
    "worker_id": {"type": "string"},
    "run_id": {"type": "string"},
    "task_node_id": {"type": "string"},
    "attempt_id": {"type": "string"},
    "status": {
      "type": "string",
      "enum": ["leased", "running", "completed", "failed", "released", "expired"]
    },
    "previous_status": {
      "type": "string",
      "enum": ["leased", "running", "completed", "failed", "released", "expired"]
    },
    "expires_at": {"type": "string", "format": "date-time"},
    "heartbeat_at": {"type": "string", "format": "date-time"},
    "heartbeat_interval_seconds": {"type": "integer", "minimum": 5, "maximum": 60},
    "error": {"type": "string"},
    "summary": {"type": "string"}
  }
}
```

### Queue Payload Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "https://harness.lab/schemas/ws-queue-payload.json",
  "title": "QueueEventPayload",
  "type": "object",
  "properties": {
    "shard": {"type": "string", "default": "default"},
    "depth": {"type": "integer", "minimum": 0},
    "previous_depth": {"type": "integer", "minimum": 0},
    "change": {"type": "integer"},
    "run_id": {"type": "string"},
    "task_node_id": {"type": "string"},
    "enqueued_at": {"type": "string", "format": "date-time"},
    "dequeued_at": {"type": "string", "format": "date-time"},
    "sample_tasks": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "run_id": {"type": "string"},
          "task_node_id": {"type": "string"}
        }
      }
    }
  }
}
```

### Health Payload Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "https://harness.lab/schemas/ws-health-payload.json",
  "title": "HealthEventPayload",
  "type": "object",
  "properties": {
    "warning_id": {"type": "string"},
    "component": {
      "type": "string",
      "enum": ["postgres", "redis", "sandbox", "knowledge", "provider", "artifacts", "workers"]
    },
    "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
    "message": {"type": "string"},
    "details": {"type": "object"},
    "suggested_action": {"type": "string"},
    "ready": {"type": "boolean"},
    "doctor_ready": {"type": "boolean"},
    "check_results": {
      "type": "object",
      "properties": {
        "postgres_ready": {"type": "boolean"},
        "redis_ready": {"type": "boolean"},
        "model_ready": {"type": "boolean"},
        "sandbox_ready": {"type": "boolean"},
        "knowledge_ready": {"type": "boolean"},
        "artifacts_ready": {"type": "boolean"}
      }
    }
  }
}
```

---

## Connection Management

### Connection Manager Design

```python
from dataclasses import dataclass, field
from typing import Dict, Set, Optional
from fastapi import WebSocket
from asyncio import Lock
import time

@dataclass
class WebSocketConnection:
    """Represents an active WebSocket connection."""
    websocket: WebSocket
    endpoint: str  # workers, queues, leases, health
    filters: Dict[str, str] = field(default_factory=dict)
    connected_at: float = field(default_factory=time.time)
    last_ping_at: float = field(default_factory=time.time)
    last_pong_at: float = field(default_factory=time.time)
    message_count: int = 0
    client_id: str = ""

class WebSocketConnectionManager:
    """Manages WebSocket connections with broadcast support."""
    
    def __init__(self):
        self._connections: Dict[str, WebSocketConnection] = {}
        self._endpoint_groups: Dict[str, Set[str]] = {
            "workers": set(),
            "queues": set(),
            "leases": set(),
            "health": set(),
        }
        self._lock = Lock()
        self._next_client_id = 0
    
    async def connect(
        self,
        websocket: WebSocket,
        endpoint: str,
        filters: Dict[str, str] = None
    ) -> str:
        """Register a new WebSocket connection."""
        await websocket.accept()
        
        client_id = f"ws_{endpoint}_{self._next_client_id}"
        self._next_client_id += 1
        
        connection = WebSocketConnection(
            websocket=websocket,
            endpoint=endpoint,
            filters=filters or {},
            client_id=client_id,
        )
        
        async with self._lock:
            self._connections[client_id] = connection
            self._endpoint_groups[endpoint].add(client_id)
        
        return client_id
    
    async def disconnect(self, client_id: str):
        """Remove a WebSocket connection."""
        async with self._lock:
            if client_id in self._connections:
                conn = self._connections.pop(client_id)
                self._endpoint_groups[conn.endpoint].discard(client_id)
    
    async def broadcast_to_endpoint(
        self,
        endpoint: str,
        message: dict,
        filters_match: Optional[Dict[str, str]] = None
    ):
        """Broadcast message to all connections on an endpoint."""
        async with self._lock:
            client_ids = list(self._endpoint_groups.get(endpoint, set()))
        
        for client_id in client_ids:
            conn = self._connections.get(client_id)
            if conn is None:
                continue
            
            # Apply filters if specified
            if filters_match and not self._matches_filters(conn.filters, filters_match):
                continue
            
            try:
                await conn.websocket.send_json(message)
                conn.message_count += 1
            except Exception:
                # Connection likely closed, schedule cleanup
                await self.disconnect(client_id)
    
    async def send_to_client(self, client_id: str, message: dict):
        """Send message to specific client."""
        conn = self._connections.get(client_id)
        if conn:
            try:
                await conn.websocket.send_json(message)
                conn.message_count += 1
            except Exception:
                await self.disconnect(client_id)
    
    def _matches_filters(
        self,
        connection_filters: Dict[str, str],
        message_filters: Dict[str, str]
    ) -> bool:
        """Check if message filters match connection filters."""
        for key, value in connection_filters.items():
            if key in message_filters and message_filters[key] != value:
                return False
        return True
    
    def get_connection_count(self, endpoint: str = None) -> int:
        """Get count of active connections."""
        if endpoint:
            return len(self._endpoint_groups.get(endpoint, set()))
        return len(self._connections)
```

### Connection Lifecycle

```
1. Client initiates WebSocket connection with query parameters
2. Server accepts connection and registers in ConnectionManager
3. Server sends initial snapshot message for current state
4. Server starts heartbeat timer for this connection
5. Server listens for incoming messages (ping, subscribe, unsubscribe)
6. Server broadcasts relevant events to connection
7. On disconnect (client close or timeout), cleanup connection
```

---

## Heartbeat Detection

### Server-Side Heartbeat

```python
import asyncio
from datetime import datetime

HEARTBEAT_INTERVAL = 30  # seconds
HEARTBEAT_TIMEOUT = 90   # seconds (3 missed heartbeats)

async def heartbeat_loop(
    connection: WebSocketConnection,
    manager: WebSocketConnectionManager
):
    """Send periodic heartbeats and check for timeout."""
    while True:
        try:
            # Check for timeout
            elapsed_since_pong = time.time() - connection.last_pong_at
            if elapsed_since_pong > HEARTBEAT_TIMEOUT:
                # Connection timed out, close it
                await connection.websocket.close(code=1001, reason="Heartbeat timeout")
                await manager.disconnect(connection.client_id)
                break
            
            # Send ping
            ping_message = {
                "type": "ping",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "payload": {
                    "interval": HEARTBEAT_INTERVAL,
                    "timeout": HEARTBEAT_TIMEOUT
                }
            }
            await connection.websocket.send_json(ping_message)
            connection.last_ping_at = time.time()
            
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            
        except Exception:
            await manager.disconnect(connection.client_id)
            break

async def handle_pong(connection: WebSocketConnection):
    """Update pong timestamp on heartbeat response."""
    connection.last_pong_at = time.time()
```

### Client-Side Heartbeat Response

Clients should respond to `ping` messages with `pong`:

```json
{
  "type": "pong",
  "timestamp": "2026-04-18T00:30:15Z",
  "payload": {
    "latency_ms": 25
  }
}
```

---

## Reconnection Mechanism

### Client Reconnection Strategy

```
1. Maintain connection state (last received timestamp, filters)
2. On disconnect, attempt reconnect with exponential backoff
3. On reconnect, send `subscribe` message with last timestamp
4. Server responds with missed events or snapshot since timestamp
5. Resume normal event stream

Backoff schedule:
- 1st retry: immediate
- 2nd retry: 1 second
- 3rd retry: 2 seconds
- 4th retry: 4 seconds
- 5th+ retry: max 30 seconds
```

### Subscription Resume Message

```json
{
  "type": "subscribe",
  "timestamp": "2026-04-18T00:30:00Z",
  "payload": {
    "last_received_timestamp": "2026-04-18T00:25:00Z",
    "filters": {
      "worker_id": "worker_abc"
    }
  }
}
```

### Server Resume Response

```json
{
  "type": "resume_snapshot",
  "timestamp": "2026-04-18T00:30:00Z",
  "payload": {
    "resumed_from": "2026-04-18T00:25:00Z",
    "missed_events": [
      // Array of events since last_received_timestamp
    ],
    "current_state": {
      // Current snapshot of subscribed entities
    }
  }
}
```

---

## Event Source Integration

### Integration Points

| Endpoint | Event Source | Trigger |
|----------|--------------|---------|
| `/ws/workers` | `WorkerRegistry` | Worker registration, heartbeat, state change |
| `/ws/queues` | `DispatchQueue` | Enqueue, dequeue, depth change |
| `/ws/leases` | `LeaseManager` | Lease lifecycle transitions |
| `/ws/health` | `RuntimeService.doctor_report()` | Periodic health checks, warning changes |

### Event Publisher Pattern

```python
from typing import Callable, Awaitable
from functools import wraps

class WebSocketEventPublisher:
    """Publishes events to WebSocket connections."""
    
    def __init__(self, connection_manager: WebSocketConnectionManager):
        self.manager = connection_manager
    
    async def publish_worker_event(
        self,
        event_type: str,
        worker_snapshot: WorkerSnapshot,
        previous_state: str = None
    ):
        """Publish worker state change event."""
        message = {
            "type": event_type,
            "timestamp": utc_now(),
            "payload": {
                "worker_id": worker_snapshot.worker_id,
                "label": worker_snapshot.label,
                "state": worker_snapshot.state,
                "previous_state": previous_state,
                "current_state": worker_snapshot.state,
                "role_profile": worker_snapshot.role_profile,
                "current_run_id": worker_snapshot.current_run_id,
                "current_task_node_id": worker_snapshot.current_task_node_id,
                "current_lease_id": worker_snapshot.current_lease_id,
                "lease_count": worker_snapshot.lease_count,
                "heartbeat_at": worker_snapshot.heartbeat_at,
                "drain_state": worker_snapshot.drain_state,
            }
        }
        
        # Broadcast with filters
        await self.manager.broadcast_to_endpoint(
            "workers",
            message,
            filters_match={
                "worker_id": worker_snapshot.worker_id,
                "role_profile": worker_snapshot.role_profile,
                "state_filter": worker_snapshot.state,
            }
        )
    
    async def publish_lease_event(
        self,
        event_type: str,
        lease: WorkerLease,
        previous_status: str = None
    ):
        """Publish lease lifecycle event."""
        message = {
            "type": event_type,
            "timestamp": utc_now(),
            "payload": {
                "lease_id": lease.lease_id,
                "worker_id": lease.worker_id,
                "run_id": lease.run_id,
                "task_node_id": lease.task_node_id,
                "attempt_id": lease.attempt_id,
                "status": lease.status,
                "previous_status": previous_status,
                "expires_at": lease.expires_at,
                "heartbeat_at": lease.heartbeat_at,
            }
        }
        
        await self.manager.broadcast_to_endpoint(
            "leases",
            message,
            filters_match={
                "lease_id": lease.lease_id,
                "run_id": lease.run_id,
                "worker_id": lease.worker_id,
            }
        )
    
    async def publish_queue_event(
        self,
        event_type: str,
        shard: str,
        depth: int,
        previous_depth: int,
        sample_tasks: list = None
    ):
        """Publish queue depth change event."""
        message = {
            "type": event_type,
            "timestamp": utc_now(),
            "payload": {
                "shard": shard,
                "depth": depth,
                "previous_depth": previous_depth,
                "change": depth - previous_depth,
                "sample_tasks": sample_tasks or [],
            }
        }
        
        await self.manager.broadcast_to_endpoint(
            "queues",
            message,
            filters_match={"shard": shard}
        )
    
    async def publish_health_event(
        self,
        event_type: str,
        component: str,
        message: str,
        severity: str = "medium",
        details: dict = None
    ):
        """Publish health/warning event."""
        payload = {
            "component": component,
            "severity": severity,
            "message": message,
            "details": details or {},
        }
        
        if event_type in ["warning_raised", "warning_resolved"]:
            payload["warning_id"] = f"warn_{component}_{int(time.time())}"
        
        ws_message = {
            "type": event_type,
            "timestamp": utc_now(),
            "payload": payload,
        }
        
        await self.manager.broadcast_to_endpoint("health", ws_message)
```

### Service Integration Example

```python
# In WorkerRegistry
class WorkerRegistry:
    def __init__(self, database: PlatformStore, ws_publisher: WebSocketEventPublisher = None):
        self._database = database
        self._ws_publisher = ws_publisher
    
    async def register_worker(self, request: WorkerRegisterRequest) -> WorkerSnapshot:
        worker = self._create_worker(request)
        self._database.upsert_row("workers", worker.model_dump())
        
        # Publish WebSocket event
        if self._ws_publisher:
            await self._ws_publisher.publish_worker_event(
                "worker_registered", worker
            )
        
        return worker
    
    async def heartbeat(self, worker_id: str, request: WorkerHeartbeatRequest) -> WorkerSnapshot:
        previous = self.get_worker(worker_id)
        updated = self._update_worker_state(worker_id, request)
        
        # Publish state change if different
        if self._ws_publisher and previous.state != updated.state:
            await self._ws_publisher.publish_worker_event(
                "worker_state_changed", updated, previous_state=previous.state
            )
        
        return updated
```

---

## FastAPI WebSocket Implementation

### Router Setup

```python
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from typing import Optional
import asyncio

router = APIRouter(tags=["websocket"])

# Global connection manager (initialized in bootstrap)
connection_manager: WebSocketConnectionManager = None
event_publisher: WebSocketEventPublisher = None

@router.websocket("/ws/workers")
async def websocket_workers(
    websocket: WebSocket,
    worker_id: Optional[str] = Query(None),
    role_profile: Optional[str] = Query(None),
    state_filter: Optional[str] = Query(None),
):
    """Worker status WebSocket stream."""
    filters = {}
    if worker_id:
        filters["worker_id"] = worker_id
    if role_profile:
        filters["role_profile"] = role_profile
    if state_filter:
        filters["state_filter"] = state_filter
    
    client_id = await connection_manager.connect(websocket, "workers", filters)
    
    try:
        # Send initial snapshot
        workers = get_runtime_service().workers.list_workers()
        await websocket.send_json({
            "type": "worker_list_snapshot",
            "timestamp": utc_now(),
            "payload": {
                "workers": [w.model_dump() for w in workers],
                "filters": filters,
            }
        })
        
        # Start heartbeat task
        heartbeat_task = asyncio.create_task(
            heartbeat_loop(connection_manager._connections[client_id], connection_manager)
        )
        
        # Message loop
        while True:
            data = await websocket.receive_json()
            
            if data.get("type") == "pong":
                await handle_pong(connection_manager._connections[client_id])
            elif data.get("type") == "subscribe":
                # Update filters
                new_filters = data.get("payload", {}).get("filters", {})
                connection_manager._connections[client_id].filters.update(new_filters)
            elif data.get("type") == "unsubscribe":
                # Clear specific filters
                for key in data.get("payload", {}).get("filter_keys", []):
                    connection_manager._connections[client_id].filters.pop(key, None)
    
    except WebSocketDisconnect:
        await connection_manager.disconnect(client_id)
    finally:
        heartbeat_task.cancel()

@router.websocket("/ws/queues")
async def websocket_queues(
    websocket: WebSocket,
    shard: Optional[str] = Query(None),
    include_samples: Optional[bool] = Query(False),
):
    """Queue depth WebSocket stream."""
    filters = {}
    if shard:
        filters["shard"] = shard
    
    client_id = await connection_manager.connect(websocket, "queues", filters)
    
    try:
        # Send initial snapshot
        queue_status = get_runtime_service().dispatcher.get_queue_status()
        await websocket.send_json({
            "type": "queue_snapshot",
            "timestamp": utc_now(),
            "payload": {
                "shards": [
                    {
                        "shard": s.shard,
                        "depth": s.depth,
                        "sample_tasks": s.sample_tasks if include_samples else []
                    }
                    for s in queue_status
                ],
                "filters": filters,
            }
        })
        
        heartbeat_task = asyncio.create_task(
            heartbeat_loop(connection_manager._connections[client_id], connection_manager)
        )
        
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "pong":
                await handle_pong(connection_manager._connections[client_id])
    
    except WebSocketDisconnect:
        await connection_manager.disconnect(client_id)
    finally:
        heartbeat_task.cancel()

@router.websocket("/ws/leases")
async def websocket_leases(
    websocket: WebSocket,
    run_id: Optional[str] = Query(None),
    worker_id: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None),
):
    """Lease status WebSocket stream."""
    filters = {}
    if run_id:
        filters["run_id"] = run_id
    if worker_id:
        filters["worker_id"] = worker_id
    if status_filter:
        filters["status_filter"] = status_filter
    
    client_id = await connection_manager.connect(websocket, "leases", filters)
    
    try:
        # Send initial snapshot
        leases = get_runtime_service().lease_manager.list_active_leases()
        await websocket.send_json({
            "type": "lease_snapshot",
            "timestamp": utc_now(),
            "payload": {
                "leases": [l.model_dump() for l in leases],
                "filters": filters,
            }
        })
        
        heartbeat_task = asyncio.create_task(
            heartbeat_loop(connection_manager._connections[client_id], connection_manager)
        )
        
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "pong":
                await handle_pong(connection_manager._connections[client_id])
    
    except WebSocketDisconnect:
        await connection_manager.disconnect(client_id)
    finally:
        heartbeat_task.cancel()

@router.websocket("/ws/health")
async def websocket_health(
    websocket: WebSocket,
    components: Optional[str] = Query(None),
    warning_only: Optional[bool] = Query(False),
):
    """System health WebSocket stream."""
    filters = {}
    if components:
        filters["components"] = components.split(",")
    if warning_only:
        filters["warning_only"] = "true"
    
    client_id = await connection_manager.connect(websocket, "health", filters)
    
    try:
        # Send initial snapshot
        doctor_report = get_harness_lab_services().doctor_report()
        await websocket.send_json({
            "type": "health_snapshot",
            "timestamp": utc_now(),
            "payload": {
                "doctor_ready": doctor_report.get("doctor_ready", False),
                "warnings": doctor_report.get("warnings", []),
                "execution_plane": doctor_report.get("execution_plane", {}),
                "provider": doctor_report.get("provider", {}),
                "filters": filters,
            }
        })
        
        heartbeat_task = asyncio.create_task(
            heartbeat_loop(connection_manager._connections[client_id], connection_manager)
        )
        
        # Periodic health check broadcast (every 60 seconds)
        health_check_task = asyncio.create_task(
            periodic_health_broadcast(client_id, filters)
        )
        
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "pong":
                await handle_pong(connection_manager._connections[client_id])
    
    except WebSocketDisconnect:
        await connection_manager.disconnect(client_id)
    finally:
        heartbeat_task.cancel()
        health_check_task.cancel()
```

### Bootstrap Integration

```python
# In bootstrap.py or main.py

def initialize_websocket_support():
    """Initialize WebSocket connection manager and event publisher."""
    global connection_manager, event_publisher
    
    connection_manager = WebSocketConnectionManager()
    event_publisher = WebSocketEventPublisher(connection_manager)
    
    # Inject publisher into services
    services = get_harness_lab_services()
    services.workers._ws_publisher = event_publisher
    services.runtime.lease_manager._ws_publisher = event_publisher
    services.dispatcher._ws_publisher = event_publisher

# Include WebSocket router in app
from fastapi import FastAPI

app = FastAPI()

# Initialize WebSocket support on startup
@app.on_event("startup")
async def startup_event():
    initialize_websocket_support()

# Include routers
from .harness_lab.control_plane.websocket import router as ws_router
app.include_router(ws_router)
```

---

## Best Practices

### 1. Message Ordering
- Use timestamps for ordering
- Include sequence numbers for critical events
- Client should handle duplicate detection

### 2. Error Handling
```json
{
  "type": "error",
  "timestamp": "2026-04-18T00:30:00Z",
  "payload": {
    "code": "invalid_filter",
    "message": "Invalid state_filter value",
    "details": {
      "valid_values": ["idle", "leased", "executing", "draining"]
    }
  }
}
```

### 3. Rate Limiting
- Limit broadcast frequency (e.g., queue depth updates max every 5 seconds)
- Aggregate rapid state changes into single messages
- Use debouncing for high-frequency events

### 4. Security
- Consider authentication for WebSocket connections (JWT in query param or header)
- Rate limit connections per client
- Validate all client messages before processing

### 5. Monitoring
- Track connection counts per endpoint
- Monitor heartbeat success rates
- Log broadcast latencies
- Alert on unusual disconnect patterns

---

## Implementation Roadmap

### Phase 1: Core Infrastructure
1. Create `WebSocketConnectionManager` and `WebSocketEventPublisher`
2. Add WebSocket router with all four endpoints
3. Implement basic heartbeat mechanism

### Phase 2: Service Integration
1. Inject publisher into `WorkerRegistry`
2. Inject publisher into `LeaseManager`
3. Inject publisher into `Dispatcher`
4. Add periodic health check broadcasting

### Phase 3: Advanced Features
1. Implement subscription/resume mechanism
2. Add filter support for targeted broadcasts
3. Create client SDK for easy integration

### Phase 4: Testing & Documentation
1. Unit tests for connection manager
2. Integration tests with mock services
3. API documentation with examples
4. Client implementation guide

---

## Client Usage Example

### JavaScript Client

```javascript
class HarnessLabWebSocket {
  constructor(baseUrl, endpoint, filters = {}) {
    this.url = `${baseUrl}/${endpoint}`;
    this.filters = filters;
    this.ws = null;
    this.reconnectAttempts = 0;
    this.maxReconnectDelay = 30000;
    this.lastReceivedTimestamp = null;
  }
  
  connect() {
    const params = new URLSearchParams(this.filters);
    this.ws = new WebSocket(`${this.url}?${params}`);
    
    this.ws.onopen = () => {
      console.log(`Connected to ${this.endpoint}`);
      this.reconnectAttempts = 0;
      
      // Resume if we have a last timestamp
      if (this.lastReceivedTimestamp) {
        this.ws.send(JSON.stringify({
          type: 'subscribe',
          timestamp: new Date().toISOString(),
          payload: {
            last_received_timestamp: this.lastReceivedTimestamp,
            filters: this.filters
          }
        }));
      }
    };
    
    this.ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      this.lastReceivedTimestamp = data.timestamp;
      
      if (data.type === 'ping') {
        this.ws.send(JSON.stringify({
          type: 'pong',
          timestamp: new Date().toISOString(),
          payload: { latency_ms: 0 }
        }));
      } else {
        this.onMessage(data);
      }
    };
    
    this.ws.onclose = () => {
      this.scheduleReconnect();
    };
    
    this.ws.onerror = (error) => {
      console.error('WebSocket error:', error);
    };
  }
  
  scheduleReconnect() {
    const delay = Math.min(
      1000 * Math.pow(2, this.reconnectAttempts),
      this.maxReconnectDelay
    );
    this.reconnectAttempts++;
    
    setTimeout(() => {
      console.log(`Reconnecting (attempt ${this.reconnectAttempts})...`);
      this.connect();
    }, delay);
  }
  
  onMessage(data) {
    // Override in subclass
    console.log('Received:', data);
  }
}

// Usage
const workersWS = new HarnessLabWebSocket('ws://localhost:8000', 'workers', {
  role_profile: 'executor'
});

workersWS.onMessage = (data) => {
  if (data.type === 'worker_state_changed') {
    console.log('Worker state changed:', data.payload);
  }
};

workersWS.connect();
```

---

## Summary

This WebSocket API design provides:

1. **Four dedicated endpoints** for Workers, Queues, Leases, and Health
2. **Standardized JSON message format** with clear schemas
3. **Connection management** with filtering and broadcasting
4. **Heartbeat mechanism** with 30s interval and 90s timeout
5. **Reconnection support** with resume from last timestamp
6. **Service integration** via event publisher pattern
7. **Client SDK pattern** for easy integration

The design integrates seamlessly with the existing Harness Lab Control Plane architecture and follows FastAPI best practices for WebSocket handling.