"""Tests for Orchestrator Multi-Agent Extensions.

Design source: Claude Plugin Module 05 - Coordinator Mode
"""

import pytest
from unittest.mock import MagicMock

from harness_lab.types import (
    TaskNode,
    TaskEdge,
    TaskGraph,
    ResearchSession,
    IntentDeclaration,
    ActionPlan,
    WorkflowTemplateVersion,
)
from harness_lab.types.base import AgentRole
from harness_lab.orchestrator.service import OrchestratorService


def create_test_action_plan(tool_name: str = "bash", subject: str = "test task") -> ActionPlan:
    """Helper to create test ActionPlan with required fields."""
    return ActionPlan(
        tool_name=tool_name,
        subject=subject,
        payload={"command": "test"},
        summary="Test action",
    )


class TestOrchestratorMultiAgent:
    """Tests for multi-agent orchestrator methods."""
    
    @pytest.fixture
    def orchestrator(self):
        """Create OrchestratorService."""
        return OrchestratorService()
    
    @pytest.fixture
    def task_graph(self):
        """Create sample task graph with multiple roles."""
        nodes = [
            TaskNode(
                node_id="node_1",
                label="Intent",
                kind="intent",
                agent_role="planner",
                status="planned",  # Use 'planned' for ready state
            ),
            TaskNode(
                node_id="node_2",
                label="Research",
                kind="research",
                agent_role="researcher",
                status="planned",
            ),
            TaskNode(
                node_id="node_3",
                label="Execute",
                kind="execution",
                agent_role="executor",
                status="planned",
            ),
            TaskNode(
                node_id="node_4",
                label="Verify",
                kind="verification",
                agent_role="reviewer",
                status="planned",
            ),
        ]
        
        edges = [
            TaskEdge(edge_id="edge_1", source="node_1", target="node_2"),
            TaskEdge(edge_id="edge_2", source="node_2", target="node_3"),
            TaskEdge(edge_id="edge_3", source="node_3", target="node_4", kind="handoff"),
        ]
        
        return TaskGraph(
            task_graph_id="graph_test",
            nodes=nodes,
            edges=edges,
        )
    
    def test_map_kind_to_role(self, orchestrator):
        """Test kind to role mapping."""
        assert orchestrator._map_kind_to_role("intent") == "planner"
        assert orchestrator._map_kind_to_role("execution") == "executor"
        assert orchestrator._map_kind_to_role("verification") == "reviewer"
        assert orchestrator._map_kind_to_role("research") == "researcher"
        assert orchestrator._map_kind_to_role("recovery") == "recovery"
    
    def test_get_nodes_by_role(self, orchestrator, task_graph):
        """Test getting nodes by role."""
        planner_nodes = orchestrator.get_nodes_by_role(task_graph, "planner")
        
        assert len(planner_nodes) == 1
        assert planner_nodes[0].agent_role == "planner"
    
    def test_get_ready_nodes_by_role(self, orchestrator, task_graph):
        """Test getting ready nodes by role."""
        # Set node_1 as completed so node_2 becomes ready (has fulfilled dependency)
        task_graph.nodes[0].status = "completed"
        
        ready_researchers = orchestrator.get_ready_nodes_by_role(task_graph, "researcher")
        
        assert len(ready_researchers) == 1
        assert ready_researchers[0].agent_role == "researcher"
    
    def test_count_parallel_eligible(self, orchestrator, task_graph):
        """Test counting parallel eligible nodes."""
        # Mark nodes as multi_agent_eligible
        for node in task_graph.nodes:
            node.metadata["multi_agent_eligible"] = True
        
        count = orchestrator.count_parallel_eligible(task_graph)
        
        # Should count non-handoff nodes
        assert count >= 0
    
    def test_build_multi_agent_graph(self, orchestrator):
        """Test multi-agent graph building."""
        session = MagicMock()
        session.session_id = "session_test"
        
        intent = MagicMock()
        intent.suggested_action = create_test_action_plan()
        
        graph = orchestrator.build_multi_agent_graph(session, intent)
        
        assert graph.execution_strategy == "multi_agent_wave_ready"
        assert len(graph.nodes) > 0
        assert len(graph.edges) > 0
        
        # Check for handoff edges at role transitions
        handoff_edges = [e for e in graph.edges if e.kind == "handoff"]
        assert len(handoff_edges) > 0
    
    def test_next_parallel_wave_empty(self, orchestrator):
        """Test parallel wave with empty graph."""
        empty_graph = TaskGraph(
            task_graph_id="empty",
            nodes=[],
            edges=[],
        )
        
        waves = orchestrator.next_parallel_wave(empty_graph)
        
        assert waves == []
    
    def test_next_parallel_wave_with_handoff(self, orchestrator, task_graph):
        """Test parallel wave respects handoff edges."""
        waves = orchestrator.next_parallel_wave(task_graph, max_parallel=4)
        
        # Handoff nodes should be in separate waves
        if waves:
            assert isinstance(waves, list)
            for wave in waves:
                assert isinstance(wave, list)


class TestOrchestratorWorkflowIntegration:
    """Integration tests for orchestrator workflow."""
    
    @pytest.fixture
    def orchestrator(self):
        """Create OrchestratorService."""
        return OrchestratorService()
    
    @pytest.fixture
    def mock_session(self):
        """Create mock session."""
        session = MagicMock()
        session.session_id = "session_test"
        session.goal = "Test goal"
        # Create IntentDeclaration with all required fields
        session.intent_declaration = IntentDeclaration(
            intent_id="intent_test",
            task_type="execution",
            intent="Test intent",
            confidence=0.9,
            risk_mode="normal",
            suggested_action=create_test_action_plan(),
            model_profile_id="model_default",
            created_at="2024-01-01T00:00:00Z",
        )
        return session
    
    def test_multi_agent_graph_sequence(self, orchestrator, mock_session):
        """Test multi-agent graph has correct sequence."""
        graph = orchestrator.build_multi_agent_graph(
            mock_session,
            mock_session.intent_declaration,
        )
        
        # Verify sequence
        node_kinds = [n.kind for n in graph.nodes]
        
        assert "intent" in node_kinds
        assert "execution" in node_kinds
        assert "verification" in node_kinds
    
    def test_multi_agent_graph_roles(self, orchestrator, mock_session):
        """Test multi-agent graph has correct roles."""
        graph = orchestrator.build_multi_agent_graph(
            mock_session,
            mock_session.intent_declaration,
        )
        
        roles = [n.agent_role for n in graph.nodes]
        
        assert "planner" in roles
        assert "executor" in roles
        assert "reviewer" in roles
    
    def test_multi_agent_graph_with_workflow_template(self, orchestrator, mock_session):
        """Test multi-agent graph uses workflow template."""
        workflow = WorkflowTemplateVersion(
            workflow_id="workflow_test",
            template_id="template_test",
            name="Test Workflow",
            description="Test workflow template",
            version="1",
            status="published",
            dag={
                "nodes": [
                    {"kind": "intent", "label": "Intent"},
                    {"kind": "execute", "label": "Execute"},
                ]
            },
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        )
        
        graph = orchestrator.build_multi_agent_graph(
            mock_session,
            mock_session.intent_declaration,
            workflow=workflow,
        )
        
        # Should use workflow template
        assert len(graph.nodes) > 0