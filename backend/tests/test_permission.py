"""Tests for Permission Manager.

Design source: Claude Plugin Module 05 - Coordinator Mode
"""

import pytest

from harness_lab.runtime.permission import (
    PermissionManager,
    PermissionMode,
    PermissionSet,
    WorkerPermissionContext,
    ROLE_PERMISSIONS,
    DANGEROUS_TOOLS,
    is_dangerous_tool,
    requires_approval,
)


class TestPermissionSet:
    """Tests for PermissionSet."""
    
    def test_permission_set_creation(self):
        """Test permission set creation."""
        perms = PermissionSet(
            allowed_tools={"read", "write"},
            denied_tools={"execute"},
        )
        
        assert perms.allowed_tools == {"read", "write"}
        assert perms.denied_tools == {"execute"}
    
    def test_is_tool_allowed_explicit_allowed(self):
        """Test tool allowed check - explicitly allowed."""
        perms = PermissionSet(
            allowed_tools={"read", "write"},
        )
        
        assert perms.is_tool_allowed("read") is True
        assert perms.is_tool_allowed("write") is True
    
    def test_is_tool_allowed_not_in_allowed(self):
        """Test tool allowed check - not in allowed."""
        perms = PermissionSet(
            allowed_tools={"read", "write"},
        )
        
        assert perms.is_tool_allowed("execute") is False
    
    def test_is_tool_allowed_denied_takes_precedence(self):
        """Test denied takes precedence over allowed."""
        perms = PermissionSet(
            allowed_tools={"read", "write", "execute"},
            denied_tools={"execute"},
        )
        
        assert perms.is_tool_allowed("execute") is False
    
    def test_is_tool_allowed_empty_allowed_means_all(self):
        """Test empty allowed means all allowed."""
        perms = PermissionSet(
            allowed_tools=set(),
            denied_tools={"execute"},
        )
        
        assert perms.is_tool_allowed("read") is True
        assert perms.is_tool_allowed("execute") is False
    
    def test_add_allowed_tool(self):
        """Test adding allowed tool."""
        perms = PermissionSet()
        
        perms.add_allowed_tool("read")
        
        assert "read" in perms.allowed_tools
        assert "read" not in perms.denied_tools
    
    def test_add_denied_tool(self):
        """Test adding denied tool."""
        perms = PermissionSet(allowed_tools={"read", "execute"})
        
        perms.add_denied_tool("execute")
        
        assert "execute" in perms.denied_tools
        assert "execute" not in perms.allowed_tools
    
    def test_merge_bubble_mode(self):
        """Test merge for bubble mode."""
        parent = PermissionSet(
            allowed_tools={"read", "write", "execute"},
            denied_tools={"execute"},
        )
        
        worker = PermissionSet(
            allowed_tools={"read", "write"},
            denied_tools=set(),
        )
        
        merged = parent.merge(worker)
        
        # Intersection of allowed
        assert merged.allowed_tools == {"read", "write"}
        # Union of denied
        assert merged.denied_tools == {"execute"}


class TestWorkerPermissionContext:
    """Tests for WorkerPermissionContext."""
    
    def test_context_creation(self):
        """Test context creation."""
        context = WorkerPermissionContext(
            worker_id="worker_1",
            role="executor",
            mode=PermissionMode.BUBBLE,
            permissions=PermissionSet(allowed_tools={"read", "write"}),
        )
        
        assert context.worker_id == "worker_1"
        assert context.role == "executor"
        assert context.mode == PermissionMode.BUBBLE
    
    def test_get_effective_permissions_bubble(self):
        """Test effective permissions - bubble mode."""
        parent = PermissionSet(
            allowed_tools={"read", "write", "execute"},
        )
        
        worker_perms = PermissionSet(
            allowed_tools={"read", "write"},
            denied_tools={"execute"},
        )
        
        context = WorkerPermissionContext(
            worker_id="worker_1",
            role="executor",
            mode=PermissionMode.BUBBLE,
            permissions=worker_perms,
            parent_permissions=parent,
        )
        
        effective = context.get_effective_permissions()
        
        # Bubble: intersect allowed, union denied
        assert "read" in effective.allowed_tools
        assert "execute" in effective.denied_tools
    
    def test_get_effective_permissions_isolated(self):
        """Test effective permissions - isolated mode."""
        worker_perms = PermissionSet(
            allowed_tools={"read"},
        )
        
        context = WorkerPermissionContext(
            worker_id="worker_1",
            role="executor",
            mode=PermissionMode.ISOLATED,
            permissions=worker_perms,
            parent_permissions=PermissionSet(allowed_tools={"read", "write"}),
        )
        
        effective = context.get_effective_permissions()
        
        # Isolated: own permissions only
        assert effective.allowed_tools == {"read"}
    
    def test_get_effective_permissions_elevated(self):
        """Test effective permissions - elevated mode."""
        context = WorkerPermissionContext(
            worker_id="worker_1",
            role="executor",
            mode=PermissionMode.ELEVATED,
            permissions=PermissionSet(denied_tools={"execute"}),
        )
        
        effective = context.get_effective_permissions()
        
        # Elevated: full permissions
        assert len(effective.denied_tools) == 0


class TestPermissionManager:
    """Tests for PermissionManager."""
    
    def test_manager_creation(self):
        """Test manager creation."""
        manager = PermissionManager()
        
        assert manager.default_mode == PermissionMode.BUBBLE
        assert manager.coordinator_permissions is not None
    
    def test_create_worker_context(self):
        """Test creating worker context."""
        manager = PermissionManager()
        
        context = manager.create_worker_context("worker_1", "executor")
        
        assert context.worker_id == "worker_1"
        assert context.role == "executor"
        assert context.mode == PermissionMode.BUBBLE
    
    def test_get_worker_permissions(self):
        """Test getting worker permissions."""
        manager = PermissionManager()
        
        manager.create_worker_context("worker_1", "executor")
        perms = manager.get_worker_permissions("worker_1")
        
        assert perms is not None
        # Executor should have read, write, bash allowed
        assert perms.is_tool_allowed("read") is True
    
    def test_preflight_check_allowed(self):
        """Test preflight check - allowed tool."""
        manager = PermissionManager()
        
        manager.create_worker_context("worker_1", "executor")
        
        allowed = manager.preflight_check("worker_1", "read")
        
        assert allowed is True
    
    def test_preflight_check_denied(self):
        """Test preflight check - denied tool."""
        manager = PermissionManager()
        
        manager.create_worker_context("worker_1", "reviewer")
        
        # Reviewer shouldn't have write_file
        allowed = manager.preflight_check("worker_1", "write_file")
        
        assert allowed is False
    
    def test_preflight_check_unknown_worker(self):
        """Test preflight check - unknown worker."""
        manager = PermissionManager()
        
        allowed = manager.preflight_check("unknown_worker", "read")
        
        assert allowed is False
    
    def test_filter_tools(self):
        """Test filtering tools."""
        manager = PermissionManager()
        
        manager.create_worker_context("worker_1", "researcher")
        
        available = ["read", "write_file", "web_search", "bash"]
        filtered = manager.filter_tools("worker_1", available)
        
        # Researcher should have read, web_search
        assert "read" in filtered
        assert "web_search" in filtered
        # But not write_file or bash
        assert "write_file" not in filtered
        assert "bash" not in filtered
    
    def test_get_denied_tools(self):
        """Test getting denied tools."""
        manager = PermissionManager()
        
        manager.create_worker_context("worker_1", "reviewer")
        
        denied = manager.get_denied_tools("worker_1")
        
        # Reviewer shouldn't have write_file, bash, execute
        assert "write_file" in denied or "bash" in denied
    
    def test_remove_worker_context(self):
        """Test removing worker context."""
        manager = PermissionManager()
        
        manager.create_worker_context("worker_1", "executor")
        manager.remove_worker_context("worker_1")
        
        perms = manager.get_worker_permissions("worker_1")
        
        assert perms is None
    
    def test_get_permission_summary(self):
        """Test permission summary."""
        manager = PermissionManager()
        
        manager.create_worker_context("worker_1", "executor")
        summary = manager.get_permission_summary()
        
        assert "default_mode" in summary
        assert "active_workers" in summary
        assert summary["active_workers"] == 1


class TestRolePermissions:
    """Tests for role-based permissions."""
    
    def test_planner_permissions(self):
        """Test planner role permissions."""
        perms = ROLE_PERMISSIONS.get("planner")
        
        assert perms is not None
        assert perms.is_tool_allowed("read") is True
        assert perms.is_tool_allowed("bash") is False
    
    def test_researcher_permissions(self):
        """Test researcher role permissions."""
        perms = ROLE_PERMISSIONS.get("researcher")
        
        assert perms is not None
        assert perms.is_tool_allowed("read") is True
        assert perms.is_tool_allowed("web_search") is True
        assert perms.is_tool_allowed("write_file") is False
    
    def test_executor_permissions(self):
        """Test executor role permissions."""
        perms = ROLE_PERMISSIONS.get("executor")
        
        assert perms is not None
        assert perms.is_tool_allowed("read") is True
        assert perms.is_tool_allowed("write_file") is True
        assert perms.is_tool_allowed("bash") is True
    
    def test_reviewer_permissions(self):
        """Test reviewer role permissions."""
        perms = ROLE_PERMISSIONS.get("reviewer")
        
        assert perms is not None
        assert perms.is_tool_allowed("read") is True
        assert perms.is_tool_allowed("write_file") is False


class TestDangerousTools:
    """Tests for dangerous tool detection."""
    
    def test_is_dangerous_tool(self):
        """Test dangerous tool detection."""
        assert is_dangerous_tool("bash") is True
        assert is_dangerous_tool("execute") is True
        assert is_dangerous_tool("write_file") is True
        assert is_dangerous_tool("read") is False
    
    def test_dangerous_tools_set(self):
        """Test dangerous tools set."""
        assert "bash" in DANGEROUS_TOOLS
        assert "execute" in DANGEROUS_TOOLS
        assert "read" not in DANGEROUS_TOOLS
    
    def test_requires_approval(self):
        """Test requires_approval function."""
        manager = PermissionManager()
        manager.create_worker_context("worker_1", "executor")
        
        # Bash is dangerous and allowed for executor
        assert requires_approval("worker_1", "bash", manager) is True
        
        # Read is not dangerous
        assert requires_approval("worker_1", "read", manager) is False
        
        # execute is denied for researcher
        manager.create_worker_context("worker_2", "researcher")
        assert requires_approval("worker_2", "execute", manager) is False


class TestPermissionMode:
    """Tests for PermissionMode enum."""
    
    def test_mode_values(self):
        """Test mode enum values."""
        assert PermissionMode.BUBBLE.value == "bubble"
        assert PermissionMode.ISOLATED.value == "isolated"
        assert PermissionMode.ELEVATED.value == "elevated"