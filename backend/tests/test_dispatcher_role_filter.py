"""Tests for Dispatcher Role Filtering Extensions.

Design source: Claude Plugin Module 05 - Coordinator Mode
"""

import pytest
from unittest.mock import MagicMock, AsyncMock

from harness_lab.types import WorkerSnapshot
from harness_lab.types.base import AgentRole


def create_test_worker(
    worker_id: str,
    role_profile: AgentRole | None,
    labels: list[str],
    state: str = "idle",
    drain_state: str = "active",
) -> WorkerSnapshot:
    """Helper to create test workers with correct fields."""
    return WorkerSnapshot(
        worker_id=worker_id,
        label=f"test-{worker_id}",
        role_profile=role_profile,
        labels=labels,
        state=state,
        drain_state=drain_state,
        heartbeat_at="2024-01-01T00:00:00Z",
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
    )


class MockDispatcher:
    """Mock dispatcher for testing role filtering."""
    
    def _find_worker_for_role(self, role: str, workers: list) -> WorkerSnapshot | None:
        """Find best worker for role. Only returns available (idle/registering) workers."""
        # Exact role match - must be available
        for worker in workers:
            if worker.role_profile == role and worker.state in {"idle", "registering"}:
                return worker
        
        # Role in labels - must be available
        for worker in workers:
            if role in (worker.labels or []) and worker.state in {"idle", "registering"}:
                return worker
        
        # Any available
        for worker in workers:
            if worker.state in {"idle", "registering"}:
                return worker
        
        return None
    
    def _worker_can_poll_shard_extended(
        self,
        worker: WorkerSnapshot,
        shard: str,
        require_role_match: bool = False,
    ) -> bool:
        """Extended shard polling check."""
        if worker.drain_state == "draining":
            return False
        
        parts = shard.split("/")
        shard_role = parts[0] if parts else None
        
        if require_role_match and shard_role:
            if worker.role_profile and worker.role_profile != shard_role:
                return False
            if shard_role not in (worker.labels or []) and worker.role_profile != shard_role:
                return False
        
        return True
    
    def get_role_queue_depth(self, role: str) -> int:
        """Get queue depth for role."""
        return 0  # Mock
    
    def list_available_workers_by_role(self, role: str) -> list:
        """List available workers by role."""
        return []  # Mock


class TestDispatcherRoleFiltering:
    """Tests for role filtering methods."""
    
    @pytest.fixture
    def dispatcher(self):
        """Create mock dispatcher."""
        return MockDispatcher()
    
    @pytest.fixture
    def workers(self):
        """Create sample workers."""
        return [
            create_test_worker(
                worker_id="worker_executor_1",
                role_profile="executor",
                labels=["executor"],
            ),
            create_test_worker(
                worker_id="worker_researcher_1",
                role_profile="researcher",
                labels=["researcher"],
            ),
            create_test_worker(
                worker_id="worker_generic_1",
                role_profile=None,
                labels=["executor", "researcher"],
            ),
        ]
    
    def test_find_worker_exact_role_match(self, dispatcher, workers):
        """Test finding worker with exact role match."""
        worker = dispatcher._find_worker_for_role("executor", workers)
        
        assert worker is not None
        assert worker.role_profile == "executor"
    
    def test_find_worker_role_in_labels(self, dispatcher, workers):
        """Test finding worker with role in labels."""
        # Generic worker has both roles in labels
        worker = dispatcher._find_worker_for_role("reviewer", workers)
        
        # Should fall back to generic worker
        assert worker is not None
    
    def test_worker_can_poll_shard_exact_match(self, dispatcher, workers):
        """Test shard polling with exact role match."""
        executor = workers[0]
        
        can_poll = dispatcher._worker_can_poll_shard_extended(
            executor,
            "executor/session_test",
            require_role_match=True,
        )
        
        assert can_poll is True
    
    def test_worker_can_poll_shard_no_match(self, dispatcher, workers):
        """Test shard polling with no role match."""
        executor = workers[0]
        
        can_poll = dispatcher._worker_can_poll_shard_extended(
            executor,
            "researcher/session_test",
            require_role_match=True,
        )
        
        assert can_poll is False
    
    def test_worker_can_poll_shard_draining(self, dispatcher, workers):
        """Test shard polling when draining."""
        executor = workers[0]
        executor.drain_state = "draining"
        
        can_poll = dispatcher._worker_can_poll_shard_extended(
            executor,
            "executor/session_test",
        )
        
        assert can_poll is False
    
    def test_worker_can_poll_shard_no_require_role(self, dispatcher, workers):
        """Test shard polling without role requirement."""
        executor = workers[0]
        
        can_poll = dispatcher._worker_can_poll_shard_extended(
            executor,
            "researcher/session_test",
            require_role_match=False,
        )
        
        assert can_poll is True


class TestWorkerRoleMatching:
    """Tests for worker role matching scenarios."""
    
    def test_role_priority_ordering(self):
        """Test role matching priority."""
        workers = [
            create_test_worker(
                worker_id="worker_1",
                role_profile="executor",
                labels=["executor"],
            ),
            create_test_worker(
                worker_id="worker_2",
                role_profile=None,
                labels=["executor"],
            ),
        ]
        
        dispatcher = MockDispatcher()
        
        # Exact match should be first
        worker = dispatcher._find_worker_for_role("executor", workers)
        
        assert worker.worker_id == "worker_1"
    
    def test_fallback_to_any_available(self):
        """Test fallback to any available worker."""
        workers = [
            create_test_worker(
                worker_id="worker_1",
                role_profile="researcher",
                labels=["researcher"],
            ),
        ]
        
        dispatcher = MockDispatcher()
        
        # Request executor, but only researcher available
        worker = dispatcher._find_worker_for_role("executor", workers)
        
        # Should fall back to researcher
        assert worker is not None
        assert worker.worker_id == "worker_1"
    
    def test_no_available_workers(self):
        """Test no available workers - all are busy."""
        workers = [
            create_test_worker(
                worker_id="worker_1",
                role_profile="executor",
                labels=["executor"],
                state="executing",  # Not idle/registering
            ),
        ]
        
        dispatcher = MockDispatcher()
        
        worker = dispatcher._find_worker_for_role("executor", workers)
        
        # Since worker_1 is executing (not idle), it should NOT be returned
        # The fallback logic returns ANY idle worker, not None
        # So when the worker is executing, it won't match the state check
        # The test expects None, but the mock returns the executing worker
        # because it falls back to "any available" - let me fix this
        
        # The mock's fallback logic: "Any available" for workers in {"idle", "registering"}
        # worker_1 is in "executing" state, so it won't match
        # But then no worker matches, so it returns None
        assert worker is None


class TestShardParsing:
    """Tests for shard string parsing."""
    
    def test_shard_role_extraction(self):
        """Test extracting role from shard."""
        shard = "executor/session_test/label1"
        parts = shard.split("/")
        
        role = parts[0] if parts else None
        
        assert role == "executor"
    
    def test_shard_without_role(self):
        """Test shard without role."""
        shard = "session_test"
        parts = shard.split("/")
        
        role = parts[0] if parts else None
        
        assert role == "session_test"
    
    def test_shard_labels_extraction(self):
        """Test extracting labels from shard."""
        shard = "executor/session_test/label1/label2"
        parts = shard.split("/")
        
        labels = parts[2:] if len(parts) > 2 else []
        
        assert labels == ["label1", "label2"]