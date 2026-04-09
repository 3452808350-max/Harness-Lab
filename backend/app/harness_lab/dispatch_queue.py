from __future__ import annotations

import json
import threading
import time
from typing import Dict, List, Optional, Tuple

import redis


def _task_ref(run_id: str, task_node_id: str) -> str:
    return json.dumps({"run_id": run_id, "task_node_id": task_node_id}, ensure_ascii=False, sort_keys=True)


def _decode_task_ref(payload: str) -> Tuple[str, str]:
    data = json.loads(payload)
    return str(data["run_id"]), str(data["task_node_id"])


class DispatchQueue:
    """Redis-backed ready queue plus lease expiry index."""

    def __init__(self, redis_url: str, namespace: str = "harness_lab") -> None:
        self.redis_url = redis_url
        self.namespace = namespace
        self.client = redis.from_url(redis_url, decode_responses=True)
        self.ready_key = f"{namespace}:dispatch:ready"
        self.lease_key = f"{namespace}:dispatch:lease_expiry"

    def ping(self) -> None:
        self.client.ping()

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:  # noqa: BLE001
            return None

    def reset(self) -> None:
        self.client.delete(self.ready_key, self.lease_key)

    def enqueue_ready_task(self, run_id: str, task_node_id: str, score: Optional[float] = None) -> None:
        self.client.zadd(self.ready_key, {_task_ref(run_id, task_node_id): score or time.time()})

    def requeue_ready_task(self, run_id: str, task_node_id: str, delay_seconds: float = 0.25) -> None:
        self.enqueue_ready_task(run_id, task_node_id, score=time.time() + delay_seconds)

    def pop_ready_task(self) -> Optional[Tuple[str, str]]:
        items = self.client.zpopmin(self.ready_key, count=1)
        if not items:
            return None
        member, _score = items[0]
        return _decode_task_ref(member)

    def ready_queue_depth(self) -> int:
        return int(self.client.zcard(self.ready_key))

    def track_lease_expiry(self, lease_id: str, expires_at_epoch: float) -> None:
        self.client.zadd(self.lease_key, {lease_id: expires_at_epoch})

    def clear_lease(self, lease_id: str) -> None:
        self.client.zrem(self.lease_key, lease_id)

    def pop_expired_leases(self, now_epoch: Optional[float] = None) -> List[str]:
        deadline = now_epoch or time.time()
        expired = self.client.zrangebyscore(self.lease_key, min="-inf", max=deadline)
        if expired:
            self.client.zremrangebyscore(self.lease_key, min="-inf", max=deadline)
        return [str(item) for item in expired]


class InMemoryDispatchQueue:
    """Minimal queue for local tests where Redis is intentionally absent."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ready: Dict[str, float] = {}
        self._leases: Dict[str, float] = {}

    def ping(self) -> None:
        return None

    def close(self) -> None:
        return None

    def reset(self) -> None:
        with self._lock:
            self._ready.clear()
            self._leases.clear()

    def enqueue_ready_task(self, run_id: str, task_node_id: str, score: Optional[float] = None) -> None:
        with self._lock:
            self._ready[_task_ref(run_id, task_node_id)] = score or time.time()

    def requeue_ready_task(self, run_id: str, task_node_id: str, delay_seconds: float = 0.25) -> None:
        self.enqueue_ready_task(run_id, task_node_id, score=time.time() + delay_seconds)

    def pop_ready_task(self) -> Optional[Tuple[str, str]]:
        with self._lock:
            if not self._ready:
                return None
            member = min(self._ready, key=self._ready.get)
            self._ready.pop(member, None)
        return _decode_task_ref(member)

    def ready_queue_depth(self) -> int:
        with self._lock:
            return len(self._ready)

    def track_lease_expiry(self, lease_id: str, expires_at_epoch: float) -> None:
        with self._lock:
            self._leases[lease_id] = expires_at_epoch

    def clear_lease(self, lease_id: str) -> None:
        with self._lock:
            self._leases.pop(lease_id, None)

    def pop_expired_leases(self, now_epoch: Optional[float] = None) -> List[str]:
        deadline = now_epoch or time.time()
        with self._lock:
            expired = [lease_id for lease_id, expires_at in self._leases.items() if expires_at <= deadline]
            for lease_id in expired:
                self._leases.pop(lease_id, None)
        return expired
