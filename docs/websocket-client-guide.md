# WebSocket Client Integration Guide

## Quick Start

### JavaScript/TypeScript

```typescript
import { HarnessLabWebSocket } from './harness-websocket';

// Connect to worker events
const workers = new HarnessLabWebSocket({
  baseUrl: 'ws://localhost:4600',
  endpoint: 'workers',
  filters: { role_profile: 'executor' }
});

workers.onMessage((data) => {
  switch (data.type) {
    case 'worker_state_changed':
      console.log('Worker changed:', data.payload);
      break;
    case 'worker_list_snapshot':
      console.log('Initial workers:', data.payload.total_count);
      break;
  }
});

workers.connect();
```

### Python

```python
import asyncio
import websockets
import json

async def listen_to_workers():
    uri = "ws://localhost:4600/ws/workers?role_profile=executor"
    
    async with websockets.connect(uri) as ws:
        # Handle initial snapshot
        message = await ws.recv()
        data = json.loads(message)
        print(f"Initial workers: {data['payload']['total_count']}")
        
        while True:
            message = await ws.recv()
            data = json.loads(message)
            
            if data['type'] == 'ping':
                await ws.send(json.dumps({
                    'type': 'pong',
                    'timestamp': datetime.utcnow().isoformat() + 'Z',
                    'payload': {'latency_ms': 0}
                }))
            elif data['type'] == 'worker_state_changed':
                print(f"Worker {data['payload']['worker_id']} -> {data['payload']['current_state']}")

asyncio.run(listen_to_workers())
```

## Message Types Reference

### Workers Endpoint (`/ws/workers`)

| Type | Payload Fields |
|------|----------------|
| `worker_list_snapshot` | `workers`, `total_count`, `filters` |
| `worker_registered` | `worker_id`, `label`, `state`, `role_profile`, `capabilities` |
| `worker_state_changed` | `worker_id`, `previous_state`, `current_state`, `current_run_id` |
| `worker_heartbeat` | `worker_id`, `heartbeat_at`, `lease_count` |
| `worker_draining` | `worker_id`, `drain_state` |
| `worker_offline` | `worker_id`, `last_error` |

### Queues Endpoint (`/ws/queues`)

| Type | Payload Fields |
|------|----------------|
| `queue_snapshot` | `shards`, `total_depth`, `filters` |
| `queue_depth_update` | `shard`, `depth`, `previous_depth`, `change` |
| `queue_task_enqueued` | `shard`, `run_id`, `task_node_id`, `enqueued_at` |
| `queue_task_dequeued` | `shard`, `run_id`, `task_node_id`, `dequeued_at` |

### Leases Endpoint (`/ws/leases`)

| Type | Payload Fields |
|------|----------------|
| `lease_snapshot` | `leases`, `total_count`, `filters` |
| `lease_created` | `lease_id`, `worker_id`, `run_id`, `expires_at` |
| `lease_running` | `lease_id`, `status` |
| `lease_completed` | `lease_id`, `summary` |
| `lease_failed` | `lease_id`, `error` |
| `lease_expired` | `lease_id`, `expires_at` |

### Health Endpoint (`/ws/health`)

| Type | Payload Fields |
|------|----------------|
| `health_snapshot` | `doctor_ready`, `check_results`, `warnings`, `filters` |
| `health_check_result` | `check_results`, `doctor_ready`, `warnings_count` |
| `warning_raised` | `warning_id`, `component`, `severity`, `message` |
| `warning_resolved` | `warning_id`, `component` |

## Filter Parameters

### Workers
- `worker_id`: Filter by specific worker
- `role_profile`: Filter by role (planner, researcher, executor, reviewer, recovery)
- `state_filter`: Filter by state (idle, leased, executing, draining, offline, unhealthy)

### Queues
- `shard`: Filter by shard name (default: all shards)
- `include_samples`: Include sample task references

### Leases
- `run_id`: Filter by run
- `worker_id`: Filter by worker
- `status_filter`: Filter by status (leased, running, completed, expired)

### Health
- `components`: Filter components (postgres, redis, sandbox, knowledge, provider, artifacts)
- `warning_only`: Only push warnings

## Reconnection Pattern

```typescript
class ResilientWebSocket {
  private reconnectAttempts = 0;
  private maxDelay = 30000;
  private lastTimestamp: string | null = null;
  
  connect() {
    this.ws = new WebSocket(this.url);
    
    this.ws.onclose = () => {
      const delay = Math.min(
        1000 * Math.pow(2, this.reconnectAttempts),
        this.maxDelay
      );
      this.reconnectAttempts++;
      
      setTimeout(() => this.connect(), delay);
    };
    
    this.ws.onopen = () => {
      this.reconnectAttempts = 0;
      
      // Resume from last timestamp
      if (this.lastTimestamp) {
        this.ws.send(JSON.stringify({
          type: 'subscribe',
          timestamp: new Date().toISOString(),
          payload: {
            last_received_timestamp: this.lastTimestamp,
            filters: this.filters
          }
        }));
      }
    };
    
    this.ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      this.lastTimestamp = data.timestamp;
      this.handleMessage(data);
    };
  }
}
```

## Heartbeat Handling

Respond to `ping` messages with `pong`:

```json
{
  "type": "pong",
  "timestamp": "2026-04-18T00:30:15Z",
  "payload": {
    "latency_ms": 25
  }
}
```

If no `pong` is received within 90 seconds, the server will close the connection.

## Error Messages

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

## Schema Validation

All message payloads follow JSON schemas located at `/schemas/`:
- `ws-envelope.json` - Base envelope
- `ws-worker-payload.json` - Worker events
- `ws-lease-payload.json` - Lease events
- `ws-queue-payload.json` - Queue events
- `ws-health-payload.json` - Health events