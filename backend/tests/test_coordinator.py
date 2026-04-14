"""Tests for ParallelAgentCoordinator.

Design source: Claude Plugin Module 05 - Coordinator Mode
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from harness_lab.types.coordinator import (
    CoordinatorConfig,
    WorkflowPhase,
    WorkerResult,
    TaskDirective,
    WorkerInfo,
    CoordinatorState,
    ResearchDirection,
    ImplementationGroup,
)
from harness_lab.types.decision import TaskContext, ContinueVsSpawnDecision
from harness_lab.orchestrator.coordinator import ParallelAgentCoordinator
from harness_lab.orchestrator.decision_engine import ContinueSpawnDecisionEngine


class TestCoordinatorConfig:
    """Tests for CoordinatorConfig."""
    
    def test_default_config(self):
        """Test default configuration values."""
        config = CoordinatorConfig()
        
        assert config.max_parallel_workers == 4
        assert config.default_timeout_ms == 300000
        assert config.enable_verification is True
    
    def test_custom_config(self):
        """Test custom configuration."""
        config = CoordinatorConfig(
            max_parallel_workers=8,
            default_timeout_ms=600000,
            enable_verification=False,
        )
        
        assert config.max_parallel_workers == 8
        assert config.default_timeout_ms == 600000
        assert config.enable_verification is False
    
    def test_token_budget_allocation(self):
        """Test token budget allocation."""
        config = CoordinatorConfig()
        
        assert "research" in config.token_budget_allocation
        assert "implementation" in config.token_budget_allocation
        assert "verification" in config.token_budget_allocation
        
        # Verify allocation sums to ~1.0
        total = sum(config.token_budget_allocation.values())
        assert 0.9 <= total <= 1.1


class TestWorkflowPhase:
    """Tests for WorkflowPhase."""
    
    def test_phase_creation(self):
        """Test phase creation."""
        phase = WorkflowPhase(name="research")
        
        assert phase.name == "research"
        assert phase.status == "pending"
        assert phase.is_running() is False
        assert phase.is_terminal() is False
    
    def test_phase_start(self):
        """Test phase start."""
        phase = WorkflowPhase(name="research")
        phase.start()
        
        assert phase.is_running() is True
        assert phase.started_at is not None
    
    def test_phase_complete(self):
        """Test phase completion."""
        phase = WorkflowPhase(name="research")
        phase.start()
        phase.complete()
        
        assert phase.is_terminal() is True
        assert phase.status == "completed"
        assert phase.completed_at is not None
    
    def test_phase_fail(self):
        """Test phase failure."""
        phase = WorkflowPhase(name="research")
        phase.start()
        phase.fail()
        
        assert phase.is_terminal() is True
        assert phase.status == "failed"


class TestWorkerResult:
    """Tests for WorkerResult."""
    
    def test_successful_result(self):
        """Test successful worker result."""
        result = WorkerResult(
            worker_id="worker_test",
            success=True,
            output="Task completed",
            scope="Implement feature X",
        )
        
        assert result.success is True
        assert result.has_issues() is False
        assert result.has_file_changes() is False
    
    def test_result_with_issues(self):
        """Test result with issues."""
        result = WorkerResult(
            worker_id="worker_test",
            success=True,
            output="Task completed",
            scope="Test",
            issues=["Issue 1", "Issue 2"],
        )
        
        assert result.has_issues() is True
    
    def test_result_with_file_changes(self):
        """Test result with file changes."""
        result = WorkerResult(
            worker_id="worker_test",
            success=True,
            output="Modified files",
            scope="Test",
            files_changed=[{"path": "src/main.py", "commit": "abc123"}],
        )
        
        assert result.has_file_changes() is True


class TestTaskDirective:
    """Tests for TaskDirective."""
    
    def test_directive_creation(self):
        """Test directive creation."""
        directive = TaskDirective(
            role="executor",
            directive="Implement feature X",
            files=["src/main.py"],
        )
        
        assert directive.role == "executor"
        assert directive.directive == "Implement feature X"
        assert len(directive.files) == 1
    
    def test_get_scope_declaration(self):
        """Test scope declaration."""
        directive = TaskDirective(
            role="executor",
            directive="Test",
            scope_declaration="Custom scope",
        )
        
        assert directive.get_scope_declaration() == "Custom scope"
    
    def test_default_scope_declaration(self):
        """Test default scope declaration."""
        directive = TaskDirective(
            role="executor",
            directive="Implement feature X",
        )
        
        assert "feature X" in directive.get_scope_declaration()


class TestImplementationGroup:
    """Tests for ImplementationGroup."""
    
    def test_sequential_group(self):
        """Test sequential group (same file)."""
        group = ImplementationGroup(
            group_id="group_1",
            files=["src/main.py"],
            sequential=True,
        )
        
        assert group.get_parallel_count() == 1
    
    def test_parallel_group(self):
        """Test parallel group (different files)."""
        group = ImplementationGroup(
            group_id="group_1",
            files=["src/a.py", "src/b.py"],
            sequential=False,
            tasks=[
                TaskDirective(role="executor", directive="Fix a.py"),
                TaskDirective(role="executor", directive="Fix b.py"),
            ],
        )
        
        assert group.get_parallel_count() == 2


class TestCoordinatorState:
    """Tests for CoordinatorState."""
    
    def test_state_creation(self):
        """Test state creation."""
        state = CoordinatorState(
            session_id="session_test",
            run_id="run_test",
        )
        
        assert state.session_id == "session_test"
        assert state.run_id == "run_test"
        assert state.get_active_worker_count() == 0
        assert state.get_completed_result_count() == 0
    
    def test_add_worker(self):
        """Test adding worker."""
        state = CoordinatorState(
            session_id="session_test",
            run_id="run_test",
        )
        
        worker = WorkerInfo(
            worker_id="worker_1",
            role="executor",
            directive=TaskDirective(role="executor", directive="Test"),
            status="running",
        )
        
        state.add_worker(worker)
        
        assert state.get_active_worker_count() == 1
    
    def test_complete_worker(self):
        """Test completing worker."""
        state = CoordinatorState(
            session_id="session_test",
            run_id="run_test",
        )
        
        worker = WorkerInfo(
            worker_id="worker_1",
            role="executor",
            directive=TaskDirective(role="executor", directive="Test"),
            status="running",
        )
        
        state.add_worker(worker)
        
        result = WorkerResult(
            worker_id="worker_1",
            success=True,
            output="Done",
            scope="Test",
        )
        
        state.complete_worker("worker_1", result)
        
        assert state.get_active_worker_count() == 0
        assert state.get_completed_result_count() == 1


class TestParallelAgentCoordinator:
    """Tests for coordinator methods."""
    
    @pytest.fixture
    def mock_orchestrator(self):
        """Create mock orchestrator."""
        orchestrator = MagicMock()
        orchestrator.next_wave.return_value = []
        return orchestrator
    
    @pytest.fixture
    def mock_dispatcher(self):
        """Create mock dispatcher."""
        dispatcher = MagicMock()
        return dispatcher
    
    @pytest.fixture
    def mock_database(self):
        """Create mock database."""
        db = MagicMock()
        db.append_event.return_value = None
        return db
    
    @pytest.fixture
    def coordinator(self, mock_orchestrator, mock_dispatcher, mock_database):
        """Create coordinator with mocks."""
        config = CoordinatorConfig()
        return ParallelAgentCoordinator(
            orchestrator=mock_orchestrator,
            dispatcher=mock_dispatcher,
            database=mock_database,
            config=config,
        )
    
    def test_coordinator_creation(self, coordinator):
        """Test coordinator creation."""
        assert coordinator.config.max_parallel_workers == 4
        assert coordinator.decision_engine is not None
    
    def test_decide_continue_or_spawn(self, coordinator):
        """Test decision method."""
        context = TaskContext(
            research_files=["src/main.py"],
            target_files=["src/main.py"],
        )
        
        decision = coordinator.decide_continue_or_spawn(context)
        
        assert decision.should_continue() is True
    
    def test_group_parallel_tasks(self, coordinator):
        """Test grouping directives."""
        directives = [
            TaskDirective(role="executor", directive="Task 1"),
            TaskDirective(role="executor", directive="Task 2"),
            TaskDirective(role="executor", directive="Task 3"),
            TaskDirective(role="executor", directive="Task 4"),
            TaskDirective(role="executor", directive="Task 5"),
        ]
        
        batches = coordinator._group_parallel_tasks(directives, max_parallel=2)
        
        # Should have 3 batches: 2, 2, 1
        assert len(batches) == 3
        assert len(batches[0]) == 2
        assert len(batches[1]) == 2
        assert len(batches[2]) == 1
    
    def test_analyze_research_directions(self, coordinator):
        """Test research direction analysis."""
        session = MagicMock()
        session.goal = "Implement feature X"
        session.intent_declaration = None
        
        directions = coordinator._analyze_research_directions(session)
        
        assert len(directions) > 0
        assert directions[0].role == "researcher"
    
    def test_synthesize_findings(self, coordinator):
        """Test synthesis."""
        results = [
            WorkerResult(
                worker_id="w1",
                success=True,
                output="Found key files",
                scope="Research",
                key_files=["src/main.py"],
            ),
        ]
        
        session = MagicMock()
        session.intent_declaration = None
        
        synthesis = coordinator._synthesize_findings(results, session)
        
        assert "summary" in synthesis
        assert "plan" in synthesis
        assert "src/main.py" in synthesis["plan"]["key_files"]
    
    def test_group_by_file(self, coordinator):
        """Test file grouping."""
        files = ["src/a.py", "src/b.py"]
        session = MagicMock()
        
        groups = coordinator._group_by_file(files, session)
        
        assert len(groups) >= 1
    
    def test_get_phase_status(self, coordinator):
        """Test phase status."""
        coordinator.state = CoordinatorState(
            session_id="session_test",
            run_id="run_test",
        )
        
        phase = coordinator.get_phase_status()
        
        assert phase is not None
        assert phase.name == "research"
    
    def test_get_active_workers(self, coordinator):
        """Test active workers list."""
        coordinator.state = CoordinatorState(
            session_id="session_test",
            run_id="run_test",
        )
        
        workers = coordinator.get_active_workers()
        
        assert workers == []


class TestCoordinatorIntegration:
    """Integration tests for coordinator workflow."""
    
    @pytest.mark.asyncio
    async def test_workflow_phases_sequence(self):
        """Test workflow executes all phases."""
        mock_orchestrator = MagicMock()
        mock_dispatcher = MagicMock()
        mock_database = MagicMock()
        mock_database.append_event.return_value = None
        
        coordinator = ParallelAgentCoordinator(
            orchestrator=mock_orchestrator,
            dispatcher=mock_dispatcher,
            database=mock_database,
            config=CoordinatorConfig(enable_verification=True),
        )
        
        session = MagicMock()
        session.session_id = "session_test"
        session.goal = "Test goal"
        session.intent_declaration = None
        
        run = MagicMock()
        run.run_id = "run_test"
        run.session_id = "session_test"
        
        # Initialize state
        coordinator.state = CoordinatorState(
            session_id=session.session_id,
            run_id=run.run_id,
        )
        
        # Test phase sequence
        assert coordinator.PHASE_SEQUENCE == ["research", "synthesis", "implementation", "verification"]