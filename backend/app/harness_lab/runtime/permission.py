"""Permission Manager for multi-agent orchestration.

Design source: Claude Plugin Module 05 - Coordinator Mode
Permission System Integration: Section 2.1.3

Key features:
    - Bubble permission mode (inherit + restrict)
    - Worker tool filtering
    - Permission preflight checks
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set


class PermissionMode(Enum):
    """Permission mode for worker execution."""
    BUBBLE = "bubble"  # Inherit parent permissions, restrict subset
    ISOLATED = "isolated"  # No inheritance, explicit permissions only
    ELEVATED = "elevated"  # Full permissions (dangerous, requires approval)


@dataclass
class PermissionSet:
    """Set of allowed/denied tools and operations."""
    allowed_tools: Set[str] = field(default_factory=set)
    denied_tools: Set[str] = field(default_factory=set)
    allowed_operations: Set[str] = field(default_factory=set)
    denied_operations: Set[str] = field(default_factory=set)
    
    def is_tool_allowed(self, tool_name: str) -> bool:
        """Check if tool is allowed.
        
        Denied takes precedence over allowed.
        
        Args:
            tool_name: Tool name
            
        Returns:
            True if allowed
        """
        if tool_name in self.denied_tools:
            return False
        
        # If no explicit allowed, default allow
        if not self.allowed_tools:
            return True
        
        return tool_name in self.allowed_tools
    
    def is_operation_allowed(self, operation: str) -> bool:
        """Check if operation is allowed.
        
        Args:
            operation: Operation name
            
        Returns:
            True if allowed
        """
        if operation in self.denied_operations:
            return False
        
        if not self.allowed_operations:
            return True
        
        return operation in self.allowed_operations
    
    def add_allowed_tool(self, tool_name: str) -> None:
        """Add allowed tool."""
        self.allowed_tools.add(tool_name)
        self.denied_tools.discard(tool_name)
    
    def add_denied_tool(self, tool_name: str) -> None:
        """Add denied tool."""
        self.denied_tools.add(tool_name)
        self.allowed_tools.discard(tool_name)
    
    def merge(self, other: PermissionSet) -> PermissionSet:
        """Merge with another permission set.
        
        In bubble mode, this creates intersection of allowed
        and union of denied.
        
        Args:
            other: Other PermissionSet
            
        Returns:
            Merged PermissionSet
        """
        return PermissionSet(
            allowed_tools=self.allowed_tools & other.allowed_tools,
            denied_tools=self.denied_tools | other.denied_tools,
            allowed_operations=self.allowed_operations & other.allowed_operations,
            denied_operations=self.denied_operations | other.denied_operations,
        )


@dataclass
class WorkerPermissionContext:
    """Permission context for a worker."""
    worker_id: str
    role: str
    mode: PermissionMode
    permissions: PermissionSet
    parent_permissions: Optional[PermissionSet] = None
    
    def get_effective_permissions(self) -> PermissionSet:
        """Get effective permissions based on mode.
        
        Bubble mode: inherit parent, then restrict
        Isolated mode: use own permissions only
        Elevated mode: full permissions
        
        Returns:
            Effective PermissionSet
        """
        if self.mode == PermissionMode.ELEVATED:
            # Full permissions (dangerous)
            return PermissionSet(
                allowed_tools=set(),  # Empty means all allowed
                denied_tools=set(),
            )
        
        if self.mode == PermissionMode.ISOLATED:
            # No inheritance
            return self.permissions
        
        if self.mode == PermissionMode.BUBBLE:
            # Inherit + restrict
            if self.parent_permissions:
                return self.parent_permissions.merge(self.permissions)
            return self.permissions
        
        return self.permissions


# Role-based default permissions
ROLE_PERMISSIONS: Dict[str, PermissionSet] = {
    "planner": PermissionSet(
        allowed_tools={"read", "glob", "grep", "web_search", "write_file"},
        denied_tools={"bash", "execute"},
    ),
    "researcher": PermissionSet(
        allowed_tools={"read", "glob", "grep", "web_search", "web_fetch"},
        denied_tools={"write_file", "bash", "execute"},
    ),
    "executor": PermissionSet(
        allowed_tools={"read", "write_file", "bash", "execute", "glob", "grep"},
        denied_tools={"web_search"},  # No external research
    ),
    "reviewer": PermissionSet(
        allowed_tools={"read", "glob", "grep"},
        denied_tools={"write_file", "bash", "execute"},
    ),
    "recovery": PermissionSet(
        allowed_tools={"read", "glob", "grep", "write_file", "bash"},
        denied_tools={"web_search", "execute"},
    ),
}


class PermissionManager:
    """Manages permissions for worker execution.
    
    Implements bubble permission mode:
    - Workers inherit coordinator's permissions
    - Each worker can restrict its own permissions further
    - Denied always takes precedence
    
    Usage:
        manager = PermissionManager()
        
        # Preflight check
        if manager.preflight_check(worker_id, tool_name):
            # Execute tool...
    
    """
    
    def __init__(
        self,
        default_mode: PermissionMode = PermissionMode.BUBBLE,
        coordinator_permissions: PermissionSet = None,
    ) -> None:
        """Initialize permission manager.
        
        Args:
            default_mode: Default PermissionMode for workers
            coordinator_permissions: Coordinator's permission set
        """
        self.default_mode = default_mode
        self.coordinator_permissions = coordinator_permissions or PermissionSet()
        self.worker_contexts: Dict[str, WorkerPermissionContext] = {}
    
    def create_worker_context(
        self,
        worker_id: str,
        role: str,
        mode: PermissionMode = None,
        custom_permissions: PermissionSet = None,
    ) -> WorkerPermissionContext:
        """Create permission context for worker.
        
        Args:
            worker_id: Worker ID
            role: Worker role
            mode: PermissionMode (defaults to self.default_mode)
            custom_permissions: Custom PermissionSet
            
        Returns:
            WorkerPermissionContext
        """
        mode = mode or self.default_mode
        
        # Start with role-based permissions
        base_permissions = ROLE_PERMISSIONS.get(role, PermissionSet())
        
        # Merge with custom permissions if provided
        if custom_permissions:
            base_permissions = base_permissions.merge(custom_permissions)
        
        context = WorkerPermissionContext(
            worker_id=worker_id,
            role=role,
            mode=mode,
            permissions=base_permissions,
            parent_permissions=self.coordinator_permissions,
        )
        
        self.worker_contexts[worker_id] = context
        
        return context
    
    def get_worker_permissions(self, worker_id: str) -> Optional[PermissionSet]:
        """Get effective permissions for worker.
        
        Args:
            worker_id: Worker ID
            
        Returns:
            Effective PermissionSet or None
        """
        context = self.worker_contexts.get(worker_id)
        if not context:
            return None
        
        return context.get_effective_permissions()
    
    def preflight_check(
        self,
        worker_id: str,
        tool_name: str,
        operation: str = None,
    ) -> bool:
        """Check if worker can use tool/operation.
        
        Preflight check before executing tool.
        
        Args:
            worker_id: Worker ID
            tool_name: Tool name
            operation: Operation name (optional)
            
        Returns:
            True if allowed
        """
        permissions = self.get_worker_permissions(worker_id)
        if not permissions:
            # No context = deny
            return False
        
        # Check tool
        if not permissions.is_tool_allowed(tool_name):
            return False
        
        # Check operation if specified
        if operation and not permissions.is_operation_allowed(operation):
            return False
        
        return True
    
    def filter_tools(
        self,
        worker_id: str,
        available_tools: List[str],
    ) -> List[str]:
        """Filter available tools based on permissions.
        
        Args:
            worker_id: Worker ID
            available_tools: List of available tool names
            
        Returns:
            Filtered list of allowed tools
        """
        permissions = self.get_worker_permissions(worker_id)
        if not permissions:
            return []
        
        return [
            tool for tool in available_tools
            if permissions.is_tool_allowed(tool)
        ]
    
    def get_denied_tools(self, worker_id: str) -> List[str]:
        """Get list of denied tools for worker.
        
        Args:
            worker_id: Worker ID
            
        Returns:
            List of denied tool names
        """
        permissions = self.get_worker_permissions(worker_id)
        if not permissions:
            return []
        
        return list(permissions.denied_tools)
    
    def update_coordinator_permissions(
        self,
        permissions: PermissionSet,
    ) -> None:
        """Update coordinator's base permissions.
        
        This affects all workers in bubble mode.
        
        Args:
            permissions: New PermissionSet
        """
        self.coordinator_permissions = permissions
        
        # Update parent permissions for all bubble mode workers
        for context in self.worker_contexts.values():
            if context.mode == PermissionMode.BUBBLE:
                context.parent_permissions = permissions
    
    def remove_worker_context(self, worker_id: str) -> None:
        """Remove worker context after completion.
        
        Args:
            worker_id: Worker ID
        """
        self.worker_contexts.pop(worker_id, None)
    
    def get_permission_summary(self) -> Dict[str, Any]:
        """Get summary of permission state.
        
        Returns:
            Dict with permission summary
        """
        return {
            "default_mode": self.default_mode.value,
            "coordinator_permissions": {
                "allowed_tools": list(self.coordinator_permissions.allowed_tools),
                "denied_tools": list(self.coordinator_permissions.denied_tools),
            },
            "active_workers": len(self.worker_contexts),
            "worker_roles": {
                worker_id: context.role
                for worker_id, context in self.worker_contexts.items()
            },
        }


# Dangerous tools that require explicit approval
DANGEROUS_TOOLS: Set[str] = {
    "bash",
    "execute",
    "write_file",
    "delete_file",
    "send_email",
    "post_tweet",
}


def is_dangerous_tool(tool_name: str) -> bool:
    """Check if tool is potentially dangerous.
    
    Args:
        tool_name: Tool name
        
    Returns:
        True if dangerous
    """
    return tool_name in DANGEROUS_TOOLS


def requires_approval(
    worker_id: str,
    tool_name: str,
    permission_manager: PermissionManager,
) -> bool:
    """Check if tool execution requires approval.
    
    Args:
        worker_id: Worker ID
        tool_name: Tool name
        permission_manager: PermissionManager
        
    Returns:
        True if approval required
    """
    # Check if tool is allowed first
    if not permission_manager.preflight_check(worker_id, tool_name):
        return False  # Denied, no approval needed
    
    # Check if dangerous
    if is_dangerous_tool(tool_name):
        return True
    
    return False