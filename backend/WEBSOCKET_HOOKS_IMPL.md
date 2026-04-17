# WebSocket Runtime Hooks Implementation

## Summary

Successfully implemented WebSocket event publishing hooks for Harness Lab Runtime components.

## Files Modified/Created

### 1. WebSocketEventPublisher (NEW)
**File**: `backend/app/harness_lab/control_plane/websocket_publisher.py`

Unified WebSocket event publisher with:
- Worker event broadcasting (registered, heartbeat, state_changed, drain, resume, offline, unhealthy)
- Lease event broadcasting (acquired, heartbeat, released, completed, failed, expired)
- Queue event broadcasting (dispatched, enqueued, depth_changed, snapshot)
- Health event broadcasting (status, fleet_summary)
- System event broadcasting
- Global publisher instance with lazy initialization
- Dependency injection support

### 2. WorkerRegistry (MODIFIED)
**File**: `backend/app/harness_lab/fleet/worker_registry.py`

WebSocket hooks added to:
- `register_worker`: broadcasts `worker.registered`
- `heartbeat`: broadcasts `worker.heartbeat` and `worker.state_changed`
- `set_drain_state`: broadcasts `worker.drain` or `worker.resume`
- `_derive_state`: broadcasts `worker.offline` or `worker.unhealthy`

New constructor parameter: `ws_publisher: Optional[WebSocketEventPublisher]`

### 3. LeaseManager (MODIFIED)
**File**: `backend/app/harness_lab/fleet/lease_manager.py`

WebSocket hooks added to:
- `heartbeat_lease`: broadcasts `lease.heartbeat`
- `complete_lease`: broadcasts `lease.completed`
- `fail_lease`: broadcasts `lease.failed`
- `release_lease`: broadcasts `lease.released`
- `_reclaim_lease`: broadcasts `lease.expired`

New constructor parameter: `ws_publisher: Optional[WebSocketEventPublisher]`

### 4. Dispatcher (MODIFIED)
**File**: `backend/app/harness_lab/fleet/dispatcher.py`

WebSocket hooks added to:
- `_create_dispatch`: broadcasts `lease.acquired` and `queue.dispatched`

New constructor parameter: `ws_publisher: Optional[WebSocketEventPublisher]`
`InMemoryDispatcher` also updated.

### 5. RuntimeService (MODIFIED)
**File**: `backend/app/harness_lab/runtime/service.py`

Changes:
- New constructor parameter: `ws_publisher: Optional[WebSocketEventPublisher]`
- Auto-initialization of WebSocket publisher if not provided
- Dependency injection into WorkerRegistry, Dispatcher, and LeaseManager
- Periodic health broadcast task (60s interval)
- New methods:
  - `start_health_broadcast()`: Start periodic health broadcast
  - `stop_health_broadcast()`: Stop periodic health broadcast
  - `_broadcast_health_status()`: Collect and broadcast health status
  - `_find_stuck_runs()`: Find runs without recent heartbeat

## Event Types

### Worker Events
| Event Type | Trigger | Data |
|------------|---------|------|
| `worker.registered` | Worker joins fleet | worker_id, label, role, capabilities, hostname, pid |
| `worker.heartbeat` | Worker heartbeat received | worker_id, state, lease_count, current_run_id |
| `worker.state_changed` | Worker state transition | worker_id, old_state, new_state |
| `worker.drain` | Worker set to draining | worker_id, reason, initiator |
| `worker.resume` | Worker resumed to active | worker_id |
| `worker.offline` | Worker went offline | worker_id, last_heartbeat_at |
| `worker.unhealthy` | Worker marked unhealthy | worker_id, error |

### Lease Events
| Event Type | Trigger | Data |
|------------|---------|------|
| `lease.acquired` | Lease acquired by worker | lease_id, worker_id, task_node_id, run_id, attempt_id |
| `lease.heartbeat` | Lease heartbeat received | lease_id, worker_id, status |
| `lease.released` | Lease released | lease_id, worker_id, reason |
| `lease.completed` | Lease completed successfully | lease_id, worker_id, task_node_id, summary |
| `lease.failed` | Lease failed | lease_id, worker_id, task_node_id, error |
| `lease.expired` | Lease expired before completion | lease_id, worker_id, task_node_id |

### Queue Events
| Event Type | Trigger | Data |
|------------|---------|------|
| `queue.dispatched` | Task dispatched from queue | shard, task_node_id, worker_id, lease_id, run_id |
| `queue.enqueued` | Task added to queue | shard, task_node_id, run_id |
| `queue.depth_changed` | Queue depth change | shard, old_depth, new_depth |
| `queue.snapshot` | Queue status snapshot | queues_data (list) |

### Health Events
| Event Type | Trigger | Data |
|------------|---------|------|
| `health.status` | Periodic (60s) | postgres_ready, redis_ready, worker_count, active_lease_count, queue_depth, draining_workers, offline_workers, unhealthy_workers, stuck_runs |
| `health.fleet_summary` | Periodic (60s) | worker_count_by_state, workers_by_role, queue_depth_by_shard, lease_reclaim_rate, stuck_run_count |

## Dependency Injection Pattern

```python
# Auto-initialization (recommended)
runtime = RuntimeService(
    database=database,
    dispatch_queue=dispatch_queue,
    context_manager=context_manager,
    constraint_engine=constraint_engine,
    tool_gateway=tool_gateway,
    model_registry=model_registry,
    orchestrator=orchestrator,
    prompt_assembler=prompt_assembler,
    # ws_publisher auto-initialized if not provided
)

# Manual initialization (for testing or custom config)
from app.harness_lab.control_plane.websocket_publisher import WebSocketEventPublisher, get_publisher

publisher = get_publisher()
runtime = RuntimeService(
    ...,
    ws_publisher=publisher,
)
```

## Periodic Health Broadcast

The RuntimeService runs a background task that broadcasts health status every 60 seconds:

```python
# Start health broadcast (typically in FastAPI lifespan)
await runtime.start_health_broadcast()

# Stop health broadcast (on shutdown)
await runtime.stop_health_broadcast()
```

Health broadcast includes:
- Worker counts by state and role
- Active lease count
- Queue depth by shard
- Draining/unhealthy/offline worker lists
- Stuck run detection (no heartbeat for 5+ minutes)

## Integration with Existing WebSocket System

The new `WebSocketEventPublisher` integrates with the existing WebSocket infrastructure in `control_plane/websocket.py`:

1. `websocket_publisher.py` provides the **synchronous** interface for Runtime hooks
2. `websocket.py` has the **async** WebSocketEventPublisher and ConnectionManager
3. Global `get_publisher()` returns a publisher connected to the existing manager

## Test Results

All tests passed:

``============================================================
Test Summary
============================================================
  Worker Hooks: ✓ PASS
  Lease Hooks: ✓ PASS
  Queue Hooks: ✓ PASS
  Health Hooks: ✓ PASS
  Dependency Injection: ✓ PASS
============================================================

🎉 ALL TESTS PASSED!
``

Test file: `backend/test_websocket_hooks.py`

## Usage Example

```python
# In WebSocket client
ws = WebSocket("ws://localhost:8000/ws/health")

# Receive health events every 60s
{
  "event_type": "health.status",
  "timestamp": 1776444847.91856,
  "data": {
    "postgres_ready": true,
    "redis_ready": true,
    "worker_count": 5,
    "active_lease_count": 3,
    "queue_depth": 10,
    "draining_workers": ["worker-001"],
    "offline_workers": ["worker-002"],
    "unhealthy_workers": [{"worker_id": "worker-003", "error": "OOM"}],
    "stuck_runs": [{"run_id": "run-001", "session_id": "session-001"}]
  }
}
```

## Next Steps

1. **FastAPI Lifespan Integration**: Add `init_websocket_publisher()` and `start_health_broadcast()` to app lifespan
2. **Frontend Integration**: Connect TUI/Web dashboard to WebSocket endpoints
3. **Filter Support**: Add client-side filtering for specific workers/leases/runs
4. **Metrics**: Add Prometheus metrics for event publishing rate

---

Implementation completed: 2026-04-18
Design source: Claude Plugin Module 05 - Coordinator Mode