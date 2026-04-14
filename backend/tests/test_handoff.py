"""Tests for AgentHandoffManager.

Design source: Claude Plugin Module 04 - Message Passing
"""

import pytest
from unittest.mock import MagicMock, patch

from harness_lab.types.base import AgentRole
from harness_lab.types.coordinator import TaskDirective
from harness_lab.runtime.handoff import AgentHandoffManager, HandoffResult


class TestAgentHandoffManager:
    """Tests for handoff creation and processing."""
    
    @pytest.fixture
    def mock_database(self):
        """Create mock database."""
        db = MagicMock()
        db.fetchone.return_value = None
        db.fetchall.return_value = []
        db.upsert_row.return_value = None
        db.append_event.return_value = None
        return db
    
    @pytest.fixture
    def mock_run(self):
        """Create mock ResearchRun."""
        run = MagicMock()
        run.run_id = "run_test123"
        run.mission_id = "mission_test123"
        run.session_id = "session_test123"
        return run
    
    @pytest.fixture
    def mock_node(self):
        """Create mock TaskNode."""
        node = MagicMock()
        node.node_id = "node_test123"
        return node
    
    def test_create_handoff(self, mock_database, mock_run, mock_node):
        """Test handoff creation."""
        manager = AgentHandoffManager(mock_database)
        
        handoff = manager.create_handoff(
            from_role="researcher",
            to_role="executor",
            run=mock_run,
            node=mock_node,
            summary="Found implementation targets",
            artifacts=["artifact_123"],
            required_action="Implement feature X",
        )
        
        assert handoff.from_role == "researcher"
        assert handoff.to_role == "executor"
        assert handoff.summary == "Found implementation targets"
        assert handoff.required_action == "Implement feature X"
        assert len(handoff.artifacts) == 1
        
        # Verify database calls
        mock_database.upsert_row.assert_called()
        mock_database.append_event.assert_called()
    
    def test_get_handoff_not_found(self, mock_database):
        """Test get_handoff raises when not found."""
        mock_database.fetchone.return_value = None
        
        manager = AgentHandoffManager(mock_database)
        
        with pytest.raises(ValueError, match="Handoff not found"):
            manager.get_handoff("nonexistent")
    
    def test_get_handoff_found(self, mock_database):
        """Test get_handoff returns handoff."""
        import json
        from harness_lab.types import HandoffPacket
        
        handoff_data = {
            "id": "handoff_test123",
            "from_role": "researcher",
            "to_role": "executor",
            "mission_id": "mission_test",
            "run_id": "run_test",
            "task_node_id": "node_test",
            "summary": "Test summary",
            "artifacts": [],
            "context_refs": [],
            "required_action": "Test action",
            "open_questions": [],
            "created_at": "2024-01-01T00:00:00Z",
        }
        mock_database.fetchone.return_value = {
            "payload_json": json.dumps(handoff_data),
        }
        
        manager = AgentHandoffManager(mock_database)
        handoff = manager.get_handoff("handoff_test123")
        
        assert handoff.id == "handoff_test123"
        assert handoff.from_role == "researcher"
    
    def test_list_pending_handoffs(self, mock_database):
        """Test listing pending handoffs."""
        mock_database.fetchall.return_value = []
        
        manager = AgentHandoffManager(mock_database)
        handoffs = manager.list_pending_handoffs("executor")
        
        assert handoffs == []
    
    def test_list_pending_handoffs_with_run_filter(self, mock_database):
        """Test listing pending handoffs with run filter."""
        mock_database.fetchall.return_value = []
        
        manager = AgentHandoffManager(mock_database)
        handoffs = manager.list_pending_handoffs("executor", run_id="run_test")
        
        assert handoffs == []
        # Verify query included run_id filter
        call_args = mock_database.fetchall.call_args
        assert "run_id" in str(call_args)
    
    def test_build_agent_context(self, mock_database):
        """Test execution context building."""
        import json
        from harness_lab.types import HandoffPacket
        
        handoff = HandoffPacket(
            id="handoff_test",
            from_role="researcher",
            to_role="executor",
            mission_id="mission_test",
            run_id="run_test",
            task_node_id="node_test",
            summary="Research complete",
            artifacts=["artifact_1"],
            context_refs=["ref_1"],
            required_action="Implement",
            open_questions=["Question 1"],
            created_at="2024-01-01T00:00:00Z",
        )
        
        manager = AgentHandoffManager(mock_database)
        context = manager._build_agent_context(handoff)
        
        assert context["from_role"] == "researcher"
        assert context["summary"] == "Research complete"
        assert context["artifacts"] == ["artifact_1"]
        assert context["required_action"] == "Implement"
        assert "directive" in context
        assert "worker_rules" in context
    
    def test_get_worker_rules_for_role(self, mock_database):
        """Test worker rules template."""
        manager = AgentHandoffManager(mock_database)
        
        rules = manager._get_worker_rules_for_role("executor")
        
        assert "forked worker" in rules.lower()
        assert "Scope:" in rules
        assert "Result:" in rules


class TestHandoffResult:
    """Tests for HandoffResult."""
    
    def test_handoff_result_creation(self):
        """Test HandoffResult model."""
        from harness_lab.types import HandoffPacket
        
        handoff = HandoffPacket(
            id="handoff_test",
            from_role="researcher",
            to_role="executor",
            mission_id="mission_test",
            run_id="run_test",
            task_node_id="node_test",
            summary="Test",
            artifacts=[],
            context_refs=[],
            required_action="Act",
            open_questions=[],
            created_at="2024-01-01T00:00:00Z",
        )
        
        result = HandoffResult(
            handoff=handoff,
            execution_context={"test": "context"},
            ready_for_execution=True,
        )
        
        assert result.ready_for_execution is True
        assert result.execution_context == {"test": "context"}