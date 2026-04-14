"""Worker Pool Manager for Fleet Worker dispatch.

Design source: Claude Plugin Module 05 - Coordinator Mode
Real Worker Dispatch Integration: Section 2.2

Key features:
    - Worker pool management
    - Role-based worker selection
    - Worker lifecycle tracking
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from ..types import WorkerSnapshot, DispatchEnvelope
from ..types.base import AgentRole
from ..types.coordinator import TaskDirective
from ..utils import new_id, utc_now


@dataclass
class WorkerPoolConfig:
    """Configuration for worker pool."""
    max_pool_size: int = 10
    min_available: int = 2
    worker_timeout_ms: int = 300000  # 5 minutes
    heartbeat_interval_ms: int = 30000  # 30 seconds
    enable_auto_scaling: bool = True


@dataclass
class PoolWorker:
    """Worker in the pool."""
    worker_id: str
    role: AgentRole
    state: str  # idle, executing, draining, terminated
    current_task: Optional[str] = None
    lease_count: int = 0
    last_heartbeat: datetime = field(default_factory=datetime.now)
    capabilities: Set[str] = field(default_factory=set)
    labels: Set[str] = field(default_factory=set)
    
    def is_available(self) -> bool:
        """Check if worker is available for task."""
        return self.state == "idle" and self.current_task is None
    
    def can_handle_role(self, role: AgentRole) -> bool:
        """Check if worker can handle role.
        
        Args:
            role: Required role
            
        Returns:
            True if worker can handle
        """
        # Exact role match
        if self.role == role:
            return True
        
        # Role in labels (flexible worker)
        if role in self.labels:
            return True
        
        # Generic worker can handle any role
        if self.role == "general":
            return True
        
        return False
    
    def assign_task(self, task_id: str) -> None:
        """Assign task to worker."""
        self.current_task = task_id
        self.state = "executing"
        self.lease_count += 1
    
    def complete_task(self) -> None:
        """Mark task as completed."""
        self.current_task = None
        self.state = "idle"
    
    def update_heartbeat(self) -> None:
        """Update heartbeat timestamp."""
        self.last_heartbeat = datetime.now()


@dataclass
class WorkerPoolState:
    """State of the worker pool."""
    total_workers: int
    available_workers: int
    executing_workers: int
    draining_workers: int
    
    role_distribution: Dict[AgentRole, int] = field(default_factory=dict)
    
    def get_available_for_role(self, role: AgentRole) -> int:
        """Get available workers for role.
        
        Args:
            role: Required role
            
        Returns:
            Number of available workers
        """
        # Exact role match
        exact = self.role_distribution.get(role, 0)
        
        # Add flexible workers (general role)
        general = self.role_distribution.get("general", 0)
        
        # Subtract executing workers
        executing_pct = self.executing_workers / self.total_workers if self.total_workers > 0 else 0
        
        available_exact = int(exact * (1 - executing_pct))
        available_general = int(general * (1 - executing_pct))
        
        return available_exact + available_general


class WorkerPoolManager:
    """Manages pool of workers for dispatch.
    
    Tracks worker availability, role matching, and
    lifecycle management.
    
    Usage:
        pool = WorkerPoolManager(dispatcher, config)
        
        # Get worker for role
        worker = pool.get_worker_for_role("executor")
        
        # Assign task
        pool.assign_task(worker.worker_id, task_id)
        
        # Complete task
        pool.complete_task(worker.worker_id)
    """
    
    def __init__(
        self,
        dispatcher: "Dispatcher",
        config: WorkerPoolConfig = None,
    ) -> None:
        """Initialize worker pool manager.
        
        Args:
            dispatcher: Dispatcher for worker communication
            config: WorkerPoolConfig (optional)
        """
        self.dispatcher = dispatcher
        self.config = config or WorkerPoolConfig()
        self.pool: Dict[str, PoolWorker] = {}
        self._initialized = False
    
    async def initialize(self) -> None:
        """Initialize pool from dispatcher's workers."""
        if self._initialized:
            return
        
        # Get workers from dispatcher
        workers = await self._fetch_workers_from_dispatcher()
        
        for worker in workers:
            pool_worker = PoolWorker(
                worker_id=worker.worker_id,
                role=worker.role_profile or "general",
                state=worker.state,
                capabilities=set(worker.capabilities or []),
                labels=set(worker.labels or []),
            )
            self.pool[worker.worker_id] = pool_worker
        
        self._initialized = True
    
    async def _fetch_workers_from_dispatcher(self) -> List[WorkerSnapshot]:
        """Fetch workers from dispatcher.
        
        Returns:
            List of WorkerSnapshot
        """
        # Use dispatcher's list_available_workers method
        try:
            workers = self.dispatcher.list_available_workers()
            return workers or []
        except Exception:
            # If dispatcher not connected, return empty
            return []
    
    def get_worker_for_role(
        self,
        role: AgentRole,
        prefer_exact_match: bool = True,
    ) -> Optional[PoolWorker]:
        """Get available worker for role.
        
        Selection priority:
        1. Exact role match (if prefer_exact_match)
        2. Role in labels
        3. General/flexible worker
        4. Any available worker
        
        Args:
            role: Required role
            prefer_exact_match: Prefer exact role match
            
        Returns:
            PoolWorker or None
        """
        available_workers = [
            w for w in self.pool.values()
            if w.is_available()
        ]
        
        if not available_workers:
            return None
        
        # Exact role match
        if prefer_exact_match:
            exact_matches = [
                w for w in available_workers
                if w.role == role
            ]
            if exact_matches:
                # Prefer worker with lowest lease count
                return min(exact_matches, key=lambda w: w.lease_count)
        
        # Role in labels
        label_matches = [
            w for w in available_workers
            if role in w.labels
        ]
        if label_matches:
            return min(label_matches, key=lambda w: w.lease_count)
        
        # General/flexible worker
        general_workers = [
            w for w in available_workers
            if w.role == "general"
        ]
        if general_workers:
            return min(general_workers, key=lambda w: w.lease_count)
        
        # Any available worker (fallback)
        return min(available_workers, key=lambda w: w.lease_count)
    
    def get_workers_for_parallel_batch(
        self,
        role: AgentRole,
        count: int,
    ) -> List[PoolWorker]:
        """Get multiple workers for parallel batch.
        
        Args:
            role: Required role
            count: Number of workers needed
            
        Returns:
            List of PoolWorker (may be less than count)
        """
        workers = []
        
        for _ in range(count):
            worker = self.get_worker_for_role(role)
            if worker:
                workers.append(worker)
            else:
                break
        
        return workers
    
    def assign_task(self, worker_id: str, task_id: str) -> bool:
        """Assign task to worker.
        
        Args:
            worker_id: Worker ID
            task_id: Task ID
            
        Returns:
            True if assignment successful
        """
        worker = self.pool.get(worker_id)
        if not worker or not worker.is_available():
            return False
        
        worker.assign_task(task_id)
        return True
    
    def complete_task(self, worker_id: str) -> None:
        """Mark worker's task as completed.
        
        Args:
            worker_id: Worker ID
        """
        worker = self.pool.get(worker_id)
        if worker:
            worker.complete_task()
    
    def get_state(self) -> WorkerPoolState:
        """Get current pool state.
        
        Returns:
            WorkerPoolState
        """
        total = len(self.pool)
        available = sum(1 for w in self.pool.values() if w.is_available())
        executing = sum(1 for w in self.pool.values() if w.state == "executing")
        draining = sum(1 for w in self.pool.values() if w.state == "draining")
        
        role_dist: Dict[AgentRole, int] = {}
        for worker in self.pool.values():
            role_dist[worker.role] = role_dist.get(worker.role, 0) + 1
        
        return WorkerPoolState(
            total_workers=total,
            available_workers=available,
            executing_workers=executing,
            draining_workers=draining,
            role_distribution=role_dist,
        )
    
    def add_worker(
        self,
        worker_id: str,
        role: AgentRole,
        capabilities: Set[str] = None,
        labels: Set[str] = None,
    ) -> PoolWorker:
        """Add new worker to pool.
        
        Args:
            worker_id: Worker ID
            role: Worker role
            capabilities: Worker capabilities
            labels: Worker labels
            
        Returns:
            PoolWorker
        """
        worker = PoolWorker(
            worker_id=worker_id,
            role=role,
            state="idle",
            capabilities=capabilities or set(),
            labels=labels or set(),
        )
        
        self.pool[worker_id] = worker
        
        return worker
    
    def remove_worker(self, worker_id: str) -> None:
        """Remove worker from pool.
        
        Args:
            worker_id: Worker ID
        """
        worker = self.pool.get(worker_id)
        if worker:
            worker.state = "terminated"
            self.pool.pop(worker_id, None)
    
    def drain_worker(self, worker_id: str) -> None:
        """Mark worker as draining (no new tasks).
        
        Args:
            worker_id: Worker ID
        """
        worker = self.pool.get(worker_id)
        if worker:
            worker.state = "draining"
    
    async def dispatch_to_worker(
        self,
        worker_id: str,
        directive: TaskDirective,
        run_id: str,
    ) -> Optional[DispatchEnvelope]:
        """Dispatch task to worker via dispatcher.
        
        Args:
            worker_id: Worker ID
            directive: TaskDirective
            run_id: Run ID
            
        Returns:
            DispatchEnvelope or None
        """
        worker = self.pool.get(worker_id)
        if not worker or not worker.is_available():
            return None
        
        # Create dispatch envelope
        envelope = DispatchEnvelope(
            dispatch_id=new_id("dispatch"),
            worker_id=worker_id,
            run_id=run_id,
            task_node_id=new_id("task"),
            scope=directive.get_scope_declaration(),
            constraints={},
            lease_duration_ms=self.config.worker_timeout_ms,
            created_at=utc_now(),
        )
        
        # Assign to worker
        worker.assign_task(envelope.task_node_id)
        
        # Dispatch via dispatcher
        try:
            await self.dispatcher.dispatch(envelope)
        except Exception as e:
            # Dispatch failed, release worker
            worker.complete_task()
            raise e
        
        return envelope
    
    def check_health(self) -> Dict[str, Any]:
        """Check pool health status.
        
        Returns:
            Dict with health status
        """
        state = self.get_state()
        
        health_issues = []
        
        # Check minimum available
        if state.available_workers < self.config.min_available:
            health_issues.append(
                f"Available workers ({state.available_workers}) below minimum ({self.config.min_available})"
            )
        
        # Check for stuck workers (executing for too long)
        for worker in self.pool.values():
            if worker.state == "executing":
                # Check if heartbeat is stale
                heartbeat_age = (datetime.now() - worker.last_heartbeat).total_seconds() * 1000
                if heartbeat_age > self.config.worker_timeout_ms:
                    health_issues.append(
                        f"Worker {worker.worker_id} may be stuck (no heartbeat for {heartbeat_age}ms)"
                    )
        
        return {
            "healthy": len(health_issues) == 0,
            "total_workers": state.total_workers,
            "available_workers": state.available_workers,
            "issues": health_issues,
        }
    
    def refresh_from_dispatcher(self) -> None:
        """Refresh pool state from dispatcher.
        
        Called periodically to sync with dispatcher.
        """
        # This would fetch latest worker states from dispatcher
        # For now, it's a placeholder for the async refresh
        pass
    
    def get_worker_by_id(self, worker_id: str) -> Optional[PoolWorker]:
        """Get worker by ID.
        
        Args:
            worker_id: Worker ID
            
        Returns:
            PoolWorker or None
        """
        return self.pool.get(worker_id)


# Integration function for coordinator
async def dispatch_parallel_workers(
    pool_manager: WorkerPoolManager,
    directives: List[TaskDirective],
    run_id: str,
    max_parallel: int,
) -> List[DispatchEnvelope]:
    """Dispatch parallel workers via pool manager.
    
    Integration point for coordinator's _execute_batch.
    
    Args:
        pool_manager: WorkerPoolManager
        directives: List of TaskDirective
        run_id: Run ID
        max_parallel: Maximum parallel workers
        
    Returns:
        List of DispatchEnvelope
    """
    envelopes = []
    
    # Limit to max_parallel
    batch_size = min(len(directives), max_parallel)
    
    for i in range(batch_size):
        directive = directives[i]
        
        # Get worker for role
        worker = pool_manager.get_worker_for_role(directive.role)
        
        if not worker:
            # No available worker, skip
            continue
        
        # Dispatch to worker
        try:
            envelope = await pool_manager.dispatch_to_worker(
                worker.worker_id,
                directive,
                run_id,
            )
            if envelope:
                envelopes.append(envelope)
        except Exception:
            # Dispatch failed, continue with other workers
            continue
    
    return envelopes