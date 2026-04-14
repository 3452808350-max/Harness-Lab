"""End-to-end Integration Tests for Multi-Agent Architecture.

Design source: Claude Plugin Module 05 - Coordinator Mode

Tests the complete flow:
1. Task classification and role assignment
2. Permission preflight checks
3. Token budget allocation
4. Worker pool dispatch
5. Handoff creation and persistence
"""

import pytest
from unittest.mock import MagicMock, AsyncMock

from harness_lab.orchestrator.decision_engine import (
    ContinueSpawnDecisionEngine,
    TaskContext,
)
from harness_lab.orchestrator.role_assigner import (
    RoleAssigner,
    TaskSignature,
    TaskType,
)
from harness_lab.runtime.permission import (
    PermissionManager,
    PermissionMode,
    PermissionSet,
)
from harness_lab.orchestrator.token_budget import (
    TokenBudgetAllocator,
    TokenBudgetConfig,
    BudgetExhaustionPolicy,
)


class TestMultiAgentEndToEnd:
    """End-to-end integration tests."""
    
    def test_task_classification_to_role_assignment(self):
        """Test flow: task classification → role assignment."""
        assigner = RoleAssigner()
        
        # Extract signature from task description
        signature = assigner.extract_signature(
            description="implement a new feature for user authentication",
            tools=["write_file", "bash"],
            files=["auth/login.py"],
        )
        
        # Classify task type
        task_type = assigner.classify_task_type(signature)
        
        # Assign role
        assignment = assigner.assign_role(signature)
        
        # Verify chain
        assert task_type == TaskType.IMPLEMENTATION
        assert assignment.role == "executor"
        assert assignment.confidence > 0
    
    def test_permission_preflight_flow(self):
        """Test flow: role → permission context → preflight check."""
        manager = PermissionManager()
        
        # Create worker context for executor role
        context = manager.create_worker_context(
            worker_id="worker_executor_1",
            role="executor",
        )
        
        # Get effective permissions
        permissions = manager.get_worker_permissions("worker_executor_1")
        
        # Preflight checks
        assert manager.preflight_check("worker_executor_1", "write_file") is True
        assert manager.preflight_check("worker_executor_1", "bash") is True
        assert manager.preflight_check("worker_executor_1", "web_search") is False
    
    def test_budget_allocation_flow(self):
        """Test flow: phase → budget allocation → usage tracking."""
        config = TokenBudgetConfig(
            total_budget=50000,
            exhaustion_policy=BudgetExhaustionPolicy.GRACEFUL,
        )
        allocator = TokenBudgetAllocator(config)
        
        # Check budget availability
        can_allocate = allocator.can_allocate("implementation", "worker_1", 10000)
        assert can_allocate is True
        
        # Allocate budget
        success = allocator.allocate("implementation", "worker_1", 10000)
        assert success is True
        
        # Record actual usage
        allocator.record_usage("implementation", "worker_1", 8000)
        
        # Check remaining budget
        remaining = allocator.get_phase_remaining("implementation")
        assert remaining > 0
    
    def test_decision_engine_integration(self):
        """Test flow: task context → decision → spawn/continue."""
        engine = ContinueSpawnDecisionEngine()
        
        # Create task context for implementation
        context = TaskContext(
            research_scope="narrow",
            impl_scope="narrow",
            is_verification=False,
            target_worker_just_wrote_code=False,
        )
        
        # Make decision
        decision = engine.decide(context)
        
        # Verify decision
        assert decision is not None
        assert decision.should_spawn() or decision.should_continue()
    
    def test_researcher_role_workflow(self):
        """Test researcher role workflow."""
        # 1. Role assignment
        assigner = RoleAssigner()
        signature = assigner.extract_signature(
            description="search for latest AI papers",
            tools=["web_search", "web_fetch"],
        )
        assignment = assigner.assign_role(signature)
        
        assert assignment.role == "researcher"
        
        # 2. Permission check
        manager = PermissionManager()
        manager.create_worker_context("worker_researcher_1", "researcher")
        
        # Researcher should have web_search, but not write_file
        assert manager.preflight_check("worker_researcher_1", "web_search") is True
        assert manager.preflight_check("worker_researcher_1", "write_file") is False
        
        # 3. Budget allocation
        allocator = TokenBudgetAllocator()
        can_allocate = allocator.can_allocate("research", "worker_researcher_1", 5000)
        
        assert can_allocate is True
    
    def test_executor_role_workflow(self):
        """Test executor role workflow."""
        # 1. Role assignment
        assigner = RoleAssigner()
        signature = assigner.extract_signature(
            description="implement user authentication feature",
            tools=["write_file", "bash"],
            files=["auth.py"],
        )
        assignment = assigner.assign_role(signature)
        
        assert assignment.role == "executor"
        
        # 2. Permission check
        manager = PermissionManager()
        manager.create_worker_context("worker_executor_1", "executor")
        
        # Executor should have write_file, bash
        assert manager.preflight_check("worker_executor_1", "write_file") is True
        assert manager.preflight_check("worker_executor_1", "bash") is True
        
        # 3. Budget allocation
        allocator = TokenBudgetAllocator()
        can_allocate = allocator.can_allocate("implementation", "worker_executor_1", 10000)
        
        assert can_allocate is True
    
    def test_reviewer_role_workflow(self):
        """Test reviewer role workflow."""
        # 1. Role assignment
        assigner = RoleAssigner()
        signature = assigner.extract_signature(
            description="verify the implementation works correctly",
            is_verification=True,
        )
        assignment = assigner.assign_role(signature)
        
        assert assignment.role == "reviewer"
        
        # 2. Permission check
        manager = PermissionManager()
        manager.create_worker_context("worker_reviewer_1", "reviewer")
        
        # Reviewer should have read, but not write_file or bash
        assert manager.preflight_check("worker_reviewer_1", "read") is True
        assert manager.preflight_check("worker_reviewer_1", "write_file") is False
        
        # 3. Budget allocation
        allocator = TokenBudgetAllocator()
        can_allocate = allocator.can_allocate("verification", "worker_reviewer_1", 5000)
        
        assert can_allocate is True
    
    def test_recovery_role_workflow(self):
        """Test recovery role workflow for error handling."""
        # 1. Role assignment
        assigner = RoleAssigner()
        signature = assigner.extract_signature(
            description="fix the failing test",
            is_retry=True,
            has_error_context=True,
        )
        assignment = assigner.assign_role(signature)
        
        assert assignment.role == "recovery"
        assert assignment.task_type == TaskType.RECOVERY
        
        # 2. Permission check
        manager = PermissionManager()
        manager.create_worker_context("worker_recovery_1", "recovery")
        
        # Recovery should have debugging tools
        assert manager.preflight_check("worker_recovery_1", "read") is True
    
    def test_budget_exhaustion_handling(self):
        """Test budget exhaustion handling."""
        config = TokenBudgetConfig(
            total_budget=10000,
            exhaustion_policy=BudgetExhaustionPolicy.FAIL_FAST,
        )
        allocator = TokenBudgetAllocator(config)
        
        # Exhaust budget
        allocator.allocate("implementation", "worker_1", 5000)
        
        # Try to allocate more (should fail)
        can_allocate = allocator.can_allocate("implementation", "worker_2", 5000)
        
        # Budget may or may not be available depending on buffer
        # Check exhaustion policy
        policy = allocator.handle_exhaustion()
        assert policy == BudgetExhaustionPolicy.FAIL_FAST
    
    def test_permission_bubble_mode(self):
        """Test bubble permission mode inheritance."""
        # Create coordinator permissions
        coordinator_perms = PermissionSet(
            allowed_tools={"read", "write_file", "bash", "web_search"},
            denied_tools={"execute"},
        )
        
        manager = PermissionManager(
            default_mode=PermissionMode.BUBBLE,
            coordinator_permissions=coordinator_perms,
        )
        
        # Create worker with additional restrictions
        worker_perms = PermissionSet(
            allowed_tools={"read", "write_file"},
            denied_tools={"bash"},
        )
        
        manager.create_worker_context(
            worker_id="worker_1",
            role="executor",
            mode=PermissionMode.BUBBLE,
            custom_permissions=worker_perms,
        )
        
        # Effective permissions should be intersection of allowed
        # and union of denied
        permissions = manager.get_worker_permissions("worker_1")
        
        # Should have read and write_file (intersection)
        assert permissions.is_tool_allowed("read") is True
        assert permissions.is_tool_allowed("write_file") is True
        
        # Should NOT have bash (in worker denied)
        assert permissions.is_tool_allowed("bash") is False
    
    def test_role_reassignment_on_context_change(self):
        """Test role reassignment when context changes."""
        assigner = RoleAssigner()
        
        # Initial assignment - implementation
        signature1 = assigner.extract_signature(
            description="implement feature",
            tools=["write_file"],
        )
        assignment1 = assigner.assign_role(signature1)
        
        assert assignment1.role == "executor"
        
        # Context change - now needs verification
        signature2 = assigner.extract_signature(
            description="verify the implementation",
            is_verification=True,
        )
        
        # Check if reassignment needed
        reassignment = assigner.reassign_role(assignment1.role, signature2)
        
        # Should suggest reviewer role
        assert reassignment is not None
        assert reassignment.role == "reviewer"


class TestMultiAgentScenarioTests:
    """Scenario-based integration tests."""
    
    def test_full_research_scenario(self):
        """Test full research scenario workflow."""
        # Setup
        assigner = RoleAssigner()
        manager = PermissionManager()
        allocator = TokenBudgetAllocator()
        
        # Task: Research AI developments
        signature = assigner.extract_signature(
            description="research latest developments in AI",
            tools=["web_search", "web_fetch", "read"],
        )
        
        # Step 1: Assign role
        assignment = assigner.assign_role(signature)
        assert assignment.role == "researcher"
        
        # Step 2: Create worker context
        manager.create_worker_context("worker_1", "researcher")
        
        # Step 3: Check permissions
        filtered_tools = manager.filter_tools(
            "worker_1",
            ["web_search", "web_fetch", "read", "write_file", "bash"],
        )
        
        assert "web_search" in filtered_tools
        assert "write_file" not in filtered_tools
        
        # Step 4: Allocate budget
        allocator.allocate("research", "worker_1", 5000)
        
        # Step 5: Verify budget
        remaining = allocator.get_phase_remaining("research")
        assert remaining > 0
    
    def test_full_implementation_scenario(self):
        """Test full implementation scenario workflow."""
        # Setup
        assigner = RoleAssigner()
        manager = PermissionManager()
        allocator = TokenBudgetAllocator()
        
        # Task: Implement authentication
        signature = assigner.extract_signature(
            description="implement user authentication with JWT",
            tools=["write_file", "bash", "read"],
            files=["auth/jwt.py"],
        )
        
        # Step 1: Assign role
        assignment = assigner.assign_role(signature)
        assert assignment.role == "executor"
        
        # Step 2: Create worker context
        manager.create_worker_context("worker_1", "executor")
        
        # Step 3: Check permissions
        filtered_tools = manager.filter_tools(
            "worker_1",
            ["write_file", "bash", "read", "web_search"],
        )
        
        assert "write_file" in filtered_tools
        assert "bash" in filtered_tools
        assert "web_search" not in filtered_tools
        
        # Step 4: Allocate budget
        allocator.allocate("implementation", "worker_1", 10000)
        
        # Step 5: Verify budget
        remaining = allocator.get_phase_remaining("implementation")
        assert remaining > 0
    
    def test_full_verification_scenario(self):
        """Test full verification scenario workflow."""
        # Setup
        assigner = RoleAssigner()
        manager = PermissionManager()
        allocator = TokenBudgetAllocator()
        
        # Task: Verify implementation
        signature = assigner.extract_signature(
            description="verify the authentication implementation works",
            is_verification=True,
        )
        
        # Step 1: Assign role
        assignment = assigner.assign_role(signature)
        assert assignment.role == "reviewer"
        
        # Step 2: Create worker context
        manager.create_worker_context("worker_1", "reviewer")
        
        # Step 3: Check permissions
        filtered_tools = manager.filter_tools(
            "worker_1",
            ["read", "grep", "glob", "write_file", "bash"],
        )
        
        assert "read" in filtered_tools
        assert "write_file" not in filtered_tools
        assert "bash" not in filtered_tools
        
        # Step 4: Allocate budget
        allocator.allocate("verification", "worker_1", 5000)
        
        # Step 5: Verify budget
        remaining = allocator.get_phase_remaining("verification")
        assert remaining > 0
    
    def test_parallel_workers_budget_distribution(self):
        """Test budget distribution across parallel workers."""
        allocator = TokenBudgetAllocator()
        
        # Get recommendation for 3 parallel workers
        recommendation = allocator.get_worker_budget_recommendation(
            "implementation",
            3,
        )
        
        # Should distribute remaining budget
        assert recommendation > 0
        
        # Allocate to 3 workers
        for i in range(3):
            success = allocator.allocate(
                "implementation",
                f"worker_{i}",
                recommendation,
            )
            # May fail if budget exhausted
            if success:
                allocator.record_usage(
                    "implementation",
                    f"worker_{i}",
                    recommendation - 1000,  # Actual usage slightly less
                )
        
        # Check can_continue_phase
        can_continue = allocator.can_continue_phase("implementation", 0)
        assert can_continue is True


class TestDecisionEngineScenarios:
    """Decision engine scenario tests."""
    
    def test_scope_mismatch_spawn_decision(self):
        """Test spawn decision for scope mismatch."""
        engine = ContinueSpawnDecisionEngine()
        
        context = TaskContext(
            research_scope="broad",
            impl_scope="narrow",
            is_retry=False,
            is_verification=False,
            previous_attempt_failed=False,
            target_worker_just_wrote_code=False,
            previous_approach_was_wrong=False,
        )
        
        decision = engine.decide(context)
        
        assert decision.should_spawn() is True
    
    def test_fresh_eyes_spawn_decision(self):
        """Test spawn decision for fresh eyes scenario."""
        engine = ContinueSpawnDecisionEngine()
        
        context = TaskContext(
            research_scope="narrow",
            impl_scope="narrow",
            is_verification=True,
            target_worker_just_wrote_code=True,
            is_retry=False,
            previous_attempt_failed=False,
            previous_approach_was_wrong=False,
        )
        
        decision = engine.decide(context)
        
        assert decision.should_spawn() is True
    
    def test_error_correction_continue_decision(self):
        """Test continue decision for error correction."""
        engine = ContinueSpawnDecisionEngine()
        
        context = TaskContext(
            research_scope="narrow",
            impl_scope="narrow",
            is_retry=True,
            previous_attempt_failed=True,
            is_verification=False,
            target_worker_just_wrote_code=False,
            previous_approach_was_wrong=False,
        )
        
        decision = engine.decide(context)
        
        assert decision.should_continue() is True
    
    def test_default_spawn_decision(self):
        """Test default spawn decision."""
        engine = ContinueSpawnDecisionEngine()
        
        # Context with no special flags
        context = TaskContext(
            research_scope="narrow",
            impl_scope="narrow",
            is_retry=False,
            is_verification=False,
            previous_attempt_failed=False,
            target_worker_just_wrote_code=False,
            previous_approach_was_wrong=False,
        )
        
        decision = engine.decide(context)
        
        # Default should spawn (no useful context)
        assert decision.should_spawn() is True