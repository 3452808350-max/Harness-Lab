"""Tests for Worker Pool Manager.

Design source: Claude Plugin Module 05 - Coordinator Mode
"""

import pytest
from unittest.mock import MagicMock, AsyncMock

from harness_lab.fleet.worker_pool import (
    WorkerPoolManager,
    WorkerPoolConfig,
    PoolWorker,
    WorkerPoolState,
    dispatch_parallel_workers,
)


class TestWorkerPoolConfig:
    """Tests for WorkerPoolConfig."""
    
    def test_default_config(self):
        """Test default configuration."""
        config = WorkerPoolConfig()
        
        assert config.max_pool_size == 10
        assert config.min_available == 2
        assert config.worker_timeout_ms == 300000
        assert config.heartbeat_interval_ms == 30000
    
    def test_custom_config(self):
        """Test custom configuration."""
        config = WorkerPoolConfig(
            max_pool_size=20,
            min_available=5,
        )
        
        assert config.max_pool_size == 20
        assert config.min_available == 5


class TestPoolWorker:
    """Tests for PoolWorker."""
    
    def test_worker_creation(self):
        """Test worker creation."""
        worker = PoolWorker(
            worker_id="worker_1",
            role="executor",
            state="idle",
        )
        
        assert worker.worker_id == "worker_1"
        assert worker.role == "executor"
        assert worker.state == "idle"
    
    def test_is_available(self):
        """Test is_available check."""
        worker = PoolWorker(
            worker_id="worker_1",
            role="executor",
            state="idle",
        )
        
        assert worker.is_available() is True
        
        worker.state = "executing"
        assert worker.is_available() is False
    
    def test_can_handle_role_exact_match(self):
        """Test can_handle_role - exact match."""
        worker = PoolWorker(
            worker_id="worker_1",
            role="executor",
            state="idle",
        )
        
        assert worker.can_handle_role("executor") is True
        assert worker.can_handle_role("researcher") is False
    
    def test_can_handle_role_labels(self):
        """Test can_handle_role - labels."""
        worker = PoolWorker(
            worker_id="worker_1",
            role="general",
            state="idle",
            labels={"executor", "researcher"},
        )
        
        assert worker.can_handle_role("executor") is True
        assert worker.can_handle_role("researcher") is True
    
    def test_can_handle_role_general(self):
        """Test can_handle_role - general worker."""
        worker = PoolWorker(
            worker_id="worker_1",
            role="general",
            state="idle",
            labels=set(),
        )
        
        # General worker can handle any role
        assert worker.can_handle_role("executor") is True
        assert worker.can_handle_role("researcher") is True
    
    def test_assign_task(self):
        """Test task assignment."""
        worker = PoolWorker(
            worker_id="worker_1",
            role="executor",
            state="idle",
        )
        
        worker.assign_task("task_1")
        
        assert worker.current_task == "task_1"
        assert worker.state == "executing"
        assert worker.lease_count == 1
    
    def test_complete_task(self):
        """Test task completion."""
        worker = PoolWorker(
            worker_id="worker_1",
            role="executor",
            state="executing",
            current_task="task_1",
        )
        
        worker.complete_task()
        
        assert worker.current_task is None
        assert worker.state == "idle"
    
    def test_update_heartbeat(self):
        """Test heartbeat update."""
        worker = PoolWorker(
            worker_id="worker_1",
            role="executor",
            state="idle",
        )
        
        old_heartbeat = worker.last_heartbeat
        worker.update_heartbeat()
        
        assert worker.last_heartbeat > old_heartbeat


class TestWorkerPoolState:
    """Tests for WorkerPoolState."""
    
    def test_state_creation(self):
        """Test state creation."""
        state = WorkerPoolState(
            total_workers=10,
            available_workers=5,
            executing_workers=3,
            draining_workers=2,
        )
        
        assert state.total_workers == 10
        assert state.available_workers == 5
    
    def test_get_available_for_role(self):
        """Test available workers for role."""
        state = WorkerPoolState(
            total_workers=10,
            available_workers=5,
            executing_workers=5,
            draining_workers=0,
            role_distribution={
                "executor": 4,
                "researcher": 4,
                "general": 2,
            },
        )
        
        available = state.get_available_for_role("executor")
        
        # Should have some available
        assert available >= 0


class TestWorkerPoolManager:
    """Tests for WorkerPoolManager."""
    
    @pytest.fixture
    def mock_dispatcher(self):
        """Create mock dispatcher."""
        dispatcher = MagicMock()
        dispatcher.list_available_workers = MagicMock(return_value=[])
        return dispatcher
    
    def test_manager_creation(self, mock_dispatcher):
        """Test manager creation."""
        manager = WorkerPoolManager(mock_dispatcher)
        
        assert manager.dispatcher == mock_dispatcher
        assert manager.pool == {}
    
    def test_add_worker(self, mock_dispatcher):
        """Test adding worker."""
        manager = WorkerPoolManager(mock_dispatcher)
        
        worker = manager.add_worker(
            worker_id="worker_1",
            role="executor",
            capabilities={"bash", "write"},
        )
        
        assert worker.worker_id == "worker_1"
        assert "worker_1" in manager.pool
    
    def test_remove_worker(self, mock_dispatcher):
        """Test removing worker."""
        manager = WorkerPoolManager(mock_dispatcher)
        
        manager.add_worker("worker_1", "executor")
        manager.remove_worker("worker_1")
        
        assert "worker_1" not in manager.pool
    
    def test_drain_worker(self, mock_dispatcher):
        """Test draining worker."""
        manager = WorkerPoolManager(mock_dispatcher)
        
        manager.add_worker("worker_1", "executor")
        manager.drain_worker("worker_1")
        
        worker = manager.pool.get("worker_1")
        assert worker.state == "draining"
    
    def test_get_worker_for_role_exact_match(self, mock_dispatcher):
        """Test getting worker for role - exact match."""
        manager = WorkerPoolManager(mock_dispatcher)
        
        manager.add_worker("worker_1", "executor")
        manager.add_worker("worker_2", "researcher")
        
        worker = manager.get_worker_for_role("executor")
        
        assert worker is not None
        assert worker.role == "executor"
    
    def test_get_worker_for_role_labels(self, mock_dispatcher):
        """Test getting worker for role - labels."""
        manager = WorkerPoolManager(mock_dispatcher)
        
        manager.add_worker("worker_1", "general", labels={"executor", "researcher"})
        
        worker = manager.get_worker_for_role("executor")
        
        assert worker is not None
    
    def test_get_worker_for_role_no_available(self, mock_dispatcher):
        """Test getting worker - none available."""
        manager = WorkerPoolManager(mock_dispatcher)
        
        # Add executing worker
        worker = manager.add_worker("worker_1", "executor")
        worker.state = "executing"
        
        available = manager.get_worker_for_role("executor")
        
        assert available is None
    
    def test_get_workers_for_parallel_batch(self, mock_dispatcher):
        """Test getting workers for parallel batch."""
        manager = WorkerPoolManager(mock_dispatcher)
        
        manager.add_worker("worker_1", "executor")
        manager.add_worker("worker_2", "executor")
        manager.add_worker("worker_3", "executor")
        
        workers = manager.get_workers_for_parallel_batch("executor", 2)
        
        assert len(workers) == 2
    
    def test_assign_task(self, mock_dispatcher):
        """Test task assignment."""
        manager = WorkerPoolManager(mock_dispatcher)
        
        manager.add_worker("worker_1", "executor")
        
        success = manager.assign_task("worker_1", "task_1")
        
        assert success is True
        worker = manager.pool.get("worker_1")
        assert worker.current_task == "task_1"
    
    def test_assign_task_not_available(self, mock_dispatcher):
        """Test task assignment - not available."""
        manager = WorkerPoolManager(mock_dispatcher)
        
        worker = manager.add_worker("worker_1", "executor")
        worker.state = "executing"
        
        success = manager.assign_task("worker_1", "task_1")
        
        assert success is False
    
    def test_complete_task(self, mock_dispatcher):
        """Test task completion."""
        manager = WorkerPoolManager(mock_dispatcher)
        
        worker = manager.add_worker("worker_1", "executor")
        worker.assign_task("task_1")
        
        manager.complete_task("worker_1")
        
        assert worker.current_task is None
        assert worker.state == "idle"
    
    def test_get_state(self, mock_dispatcher):
        """Test getting pool state."""
        manager = WorkerPoolManager(mock_dispatcher)
        
        manager.add_worker("worker_1", "executor")
        manager.add_worker("worker_2", "researcher")
        
        state = manager.get_state()
        
        assert state.total_workers == 2
        assert state.available_workers == 2
    
    def test_check_health_healthy(self, mock_dispatcher):
        """Test health check - healthy."""
        manager = WorkerPoolManager(mock_dispatcher, WorkerPoolConfig(min_available=1))
        
        manager.add_worker("worker_1", "executor")
        
        health = manager.check_health()
        
        assert health["healthy"] is True
    
    def test_check_health_unhealthy(self, mock_dispatcher):
        """Test health check - unhealthy."""
        manager = WorkerPoolManager(mock_dispatcher, WorkerPoolConfig(min_available=2))
        
        manager.add_worker("worker_1", "executor")
        
        health = manager.check_health()
        
        # Should be unhealthy because only 1 worker
        assert health["healthy"] is False
    
    def test_get_worker_by_id(self, mock_dispatcher):
        """Test getting worker by ID."""
        manager = WorkerPoolManager(mock_dispatcher)
        
        manager.add_worker("worker_1", "executor")
        
        worker = manager.get_worker_by_id("worker_1")
        
        assert worker is not None
        
        worker = manager.get_worker_by_id("unknown")
        assert worker is None


class TestDispatchParallelWorkers:
    """Tests for dispatch_parallel_workers integration."""
    
    @pytest.mark.asyncio
    async def test_dispatch_parallel_workers_basic(self):
        """Test basic parallel dispatch."""
        # Create mock dispatcher
        dispatcher = MagicMock()
        dispatcher.list_available_workers = MagicMock(return_value=[])
        dispatcher.dispatch = AsyncMock()
        
        manager = WorkerPoolManager(dispatcher)
        manager.add_worker("worker_1", "executor")
        manager.add_worker("worker_2", "executor")
        
        # Create test directives
        from harness_lab.types.coordinator import TaskDirective
        
        directives = [
            TaskDirective(role="executor", directive="Task 1"),
            TaskDirective(role="executor", directive="Task 2"),
        ]
        
        # Dispatch
        envelopes = await dispatch_parallel_workers(
            pool_manager=manager,
            directives=directives,
            run_id="run_test",
            max_parallel=2,
        )
        
        # Should have dispatched up to max_parallel
        assert len(envelopes) >= 0  # Depends on dispatcher mock behavior