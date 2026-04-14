"""Tests for Dynamic Role Assigner.

Design source: Claude Plugin Module 05 - Coordinator Mode
"""

import pytest

from harness_lab.orchestrator.role_assigner import (
    RoleAssigner,
    RoleAssignment,
    TaskSignature,
    TaskType,
    TASK_KEYWORDS,
    TOOL_TASK_TYPES,
    FILE_TYPE_ROLES,
    get_default_role_for_phase,
)


class TestTaskSignature:
    """Tests for TaskSignature."""
    
    def test_signature_creation(self):
        """Test signature creation."""
        signature = TaskSignature(
            keywords={"implement", "create"},
            tools_mentioned={"write_file"},
            file_types={".py"},
        )
        
        assert "implement" in signature.keywords
        assert "write_file" in signature.tools_mentioned
        assert ".py" in signature.file_types
    
    def test_signature_with_context_flags(self):
        """Test signature with context flags."""
        signature = TaskSignature(
            keywords=set(),
            tools_mentioned=set(),
            file_types=set(),
            is_retry=True,
            has_error_context=True,
        )
        
        assert signature.is_retry is True
        assert signature.has_error_context is True
    
    def test_to_dict(self):
        """Test to_dict conversion."""
        signature = TaskSignature(
            keywords={"test"},
            tools_mentioned={"bash"},
            file_types={".py"},
        )
        
        d = signature.to_dict()
        
        assert "keywords" in d
        assert "tools_mentioned" in d
        assert "file_types" in d


class TestTaskType:
    """Tests for TaskType enum."""
    
    def test_task_type_values(self):
        """Test task type enum values."""
        assert TaskType.RESEARCH.value == "research"
        assert TaskType.IMPLEMENTATION.value == "implementation"
        assert TaskType.VERIFICATION.value == "verification"
        assert TaskType.RECOVERY.value == "recovery"


class TestRoleAssigner:
    """Tests for RoleAssigner."""
    
    def test_assigner_creation(self):
        """Test assigner creation."""
        assigner = RoleAssigner()
        
        assert assigner.assignment_history == []
    
    def test_extract_signature_basic(self):
        """Test signature extraction - basic."""
        assigner = RoleAssigner()
        
        signature = assigner.extract_signature(
            description="implement a new feature",
        )
        
        assert "implement" in signature.keywords
        assert "feature" in signature.keywords
    
    def test_extract_signature_with_tools(self):
        """Test signature extraction - with tools."""
        assigner = RoleAssigner()
        
        signature = assigner.extract_signature(
            description="write code",
            tools=["write_file", "bash"],
        )
        
        assert "write_file" in signature.tools_mentioned
        assert "bash" in signature.tools_mentioned
    
    def test_extract_signature_with_files(self):
        """Test signature extraction - with files."""
        assigner = RoleAssigner()
        
        signature = assigner.extract_signature(
            description="fix bug",
            files=["src/main.py", "tests/test_main.py"],
        )
        
        assert ".py" in signature.file_types
    
    def test_classify_task_type_research(self):
        """Test task type classification - research."""
        assigner = RoleAssigner()
        
        signature = TaskSignature(
            keywords={"search", "find", "investigate"},
            tools_mentioned={"web_search"},
        )
        
        task_type = assigner.classify_task_type(signature)
        
        assert task_type == TaskType.RESEARCH
    
    def test_classify_task_type_implementation(self):
        """Test task type classification - implementation."""
        assigner = RoleAssigner()
        
        signature = TaskSignature(
            keywords={"implement", "create", "build"},
            tools_mentioned={"write_file"},
        )
        
        task_type = assigner.classify_task_type(signature)
        
        assert task_type == TaskType.IMPLEMENTATION
    
    def test_classify_task_type_verification(self):
        """Test task type classification - verification."""
        assigner = RoleAssigner()
        
        signature = TaskSignature(
            keywords={"verify", "test"},
            tools_mentioned={"pytest"},
            is_verification=True,
        )
        
        task_type = assigner.classify_task_type(signature)
        
        assert task_type == TaskType.VERIFICATION
    
    def test_classify_task_type_recovery(self):
        """Test task type classification - recovery."""
        assigner = RoleAssigner()
        
        signature = TaskSignature(
            keywords={"fix", "repair"},
            is_retry=True,
            has_error_context=True,
        )
        
        task_type = assigner.classify_task_type(signature)
        
        assert task_type == TaskType.RECOVERY
    
    def test_infer_role_from_task_type(self):
        """Test role inference from task type."""
        assigner = RoleAssigner()
        
        role = assigner.infer_role_from_task_type(TaskType.RESEARCH)
        assert role == "researcher"
        
        role = assigner.infer_role_from_task_type(TaskType.IMPLEMENTATION)
        assert role == "executor"
        
        role = assigner.infer_role_from_task_type(TaskType.VERIFICATION)
        assert role == "reviewer"
        
        role = assigner.infer_role_from_task_type(TaskType.PLANNING)
        assert role == "planner"
    
    def test_infer_role_from_files(self):
        """Test role inference from files."""
        assigner = RoleAssigner()
        
        signature = TaskSignature(
            keywords=set(),
            tools_mentioned=set(),
            file_types={".py"},
        )
        
        role = assigner.infer_role_from_files(signature)
        
        assert role == "executor"
    
    def test_assign_role_implementation(self):
        """Test role assignment - implementation."""
        assigner = RoleAssigner()
        
        signature = TaskSignature(
            keywords={"implement", "create"},
            tools_mentioned={"write_file"},
            file_types={".py"},
        )
        
        assignment = assigner.assign_role(signature)
        
        assert assignment.role == "executor"
        assert assignment.task_type == TaskType.IMPLEMENTATION
        assert assignment.confidence > 0
    
    def test_assign_role_research(self):
        """Test role assignment - research."""
        assigner = RoleAssigner()
        
        signature = TaskSignature(
            keywords={"search", "find"},
            tools_mentioned={"web_search"},
        )
        
        assignment = assigner.assign_role(signature)
        
        assert assignment.role == "researcher"
        assert assignment.task_type == TaskType.RESEARCH
    
    def test_assign_role_verification(self):
        """Test role assignment - verification."""
        assigner = RoleAssigner()
        
        signature = TaskSignature(
            keywords={"verify", "test"},
            is_verification=True,
        )
        
        assignment = assigner.assign_role(signature)
        
        assert assignment.role == "reviewer"
        assert assignment.task_type == TaskType.VERIFICATION
    
    def test_assign_role_recovery(self):
        """Test role assignment - recovery."""
        assigner = RoleAssigner()
        
        signature = TaskSignature(
            keywords={"fix", "debug"},
            is_retry=True,
            has_error_context=True,
        )
        
        assignment = assigner.assign_role(signature)
        
        assert assignment.role == "recovery"
        assert assignment.task_type == TaskType.RECOVERY
    
    def test_assignment_to_dict(self):
        """Test assignment to_dict."""
        assigner = RoleAssigner()
        
        signature = TaskSignature(
            keywords={"implement"},
            tools_mentioned={"write_file"},
        )
        
        assignment = assigner.assign_role(signature)
        d = assignment.to_dict()
        
        assert "role" in d
        assert "task_type" in d
        assert "confidence" in d
        assert "reasoning" in d
    
    def test_reassign_role_same_role(self):
        """Test reassignment - same role."""
        assigner = RoleAssigner()
        
        # First assignment
        signature1 = TaskSignature(
            keywords={"implement"},
            tools_mentioned={"write_file"},
        )
        assignment1 = assigner.assign_role(signature1)
        
        # New context, same role
        signature2 = TaskSignature(
            keywords={"create"},
            tools_mentioned={"write_file"},
        )
        
        reassignment = assigner.reassign_role(assignment1.role, signature2)
        
        # Same role, no reassignment needed
        assert reassignment is None
    
    def test_reassign_role_different_role(self):
        """Test reassignment - different role."""
        assigner = RoleAssigner()
        
        # First assignment - executor
        signature1 = TaskSignature(
            keywords={"implement"},
            tools_mentioned={"write_file"},
        )
        assignment1 = assigner.assign_role(signature1)
        
        # New context - research
        signature2 = TaskSignature(
            keywords={"search"},
            tools_mentioned={"web_search"},
        )
        
        reassignment = assigner.reassign_role(assignment1.role, signature2)
        
        # Different role, reassignment needed
        assert reassignment is not None
        assert reassignment.role == "researcher"
    
    def test_assignment_history(self):
        """Test assignment history tracking."""
        assigner = RoleAssigner()
        
        # Make multiple assignments
        signature1 = TaskSignature(keywords={"implement"}, tools_mentioned={"write_file"})
        signature2 = TaskSignature(keywords={"search"}, tools_mentioned={"web_search"})
        
        assigner.assign_role(signature1)
        assigner.assign_role(signature2)
        
        assert len(assigner.assignment_history) == 2
    
    def test_get_role_statistics(self):
        """Test role statistics."""
        assigner = RoleAssigner()
        
        # Make assignments
        signature1 = TaskSignature(keywords={"implement"}, tools_mentioned={"write_file"})
        signature2 = TaskSignature(keywords={"search"}, tools_mentioned={"web_search"})
        
        assigner.assign_role(signature1)
        assigner.assign_role(signature2)
        
        stats = assigner.get_role_statistics()
        
        assert stats["total_assignments"] == 2
        assert "role_distribution" in stats
        assert "task_type_distribution" in stats
        assert stats["average_confidence"] > 0


class TestRoleAssignment:
    """Tests for RoleAssignment."""
    
    def test_assignment_creation(self):
        """Test assignment creation."""
        assignment = RoleAssignment(
            role="executor",
            task_type=TaskType.IMPLEMENTATION,
            confidence=0.8,
            reasoning="Task classified as implementation",
            alternatives=["researcher"],
        )
        
        assert assignment.role == "executor"
        assert assignment.task_type == TaskType.IMPLEMENTATION
        assert assignment.confidence == 0.8
        assert "researcher" in assignment.alternatives


class TestMappings:
    """Tests for keyword/tool/file mappings."""
    
    def test_task_keywords_coverage(self):
        """Test task keywords cover all task types."""
        for task_type in TaskType:
            assert task_type in TASK_KEYWORDS
            assert len(TASK_KEYWORDS[task_type]) > 0
    
    def test_tool_task_types_coverage(self):
        """Test tool to task type mapping."""
        assert TOOL_TASK_TYPES["read"] == TaskType.RESEARCH
        assert TOOL_TASK_TYPES["write_file"] == TaskType.IMPLEMENTATION
        assert TOOL_TASK_TYPES["bash"] == TaskType.IMPLEMENTATION
    
    def test_file_type_roles_coverage(self):
        """Test file type to role mapping."""
        assert FILE_TYPE_ROLES[".py"] == "executor"
        assert FILE_TYPE_ROLES[".md"] == "reviewer"


class TestPhaseRoleDefault:
    """Tests for phase default roles."""
    
    def test_get_default_role_for_phase(self):
        """Test getting default role for phase."""
        assert get_default_role_for_phase("research") == "researcher"
        assert get_default_role_for_phase("synthesis") == "planner"
        assert get_default_role_for_phase("implementation") == "executor"
        assert get_default_role_for_phase("verification") == "reviewer"
        assert get_default_role_for_phase("unknown") == "executor"