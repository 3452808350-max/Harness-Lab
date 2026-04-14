"""Dynamic Role Assigner for multi-agent orchestration.

Design source: Claude Plugin Module 05 - Coordinator Mode
Dynamic Role Assignment: Section 2.1.4

Key features:
    - Task type recognition
    - Automatic role inference
    - Role reassignment mechanism
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from ..types.base import AgentRole


class TaskType(Enum):
    """Classification of task types."""
    RESEARCH = "research"  # Information gathering
    ANALYSIS = "analysis"  # Data/code analysis
    IMPLEMENTATION = "implementation"  # Code changes
    VERIFICATION = "verification"  # Testing/validation
    DOCUMENTATION = "documentation"  # Doc writing
    PLANNING = "planning"  # Strategy/planning
    RECOVERY = "recovery"  # Error recovery
    REVIEW = "review"  # Code review
    COMMUNICATION = "communication"  # Messaging/notifications


@dataclass
class TaskSignature:
    """Signature of a task for role inference."""
    keywords: Set[str]
    tools_mentioned: Set[str] = field(default_factory=set)
    file_types: Set[str] = field(default_factory=set)
    is_retry: bool = False
    is_verification: bool = False
    has_error_context: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict."""
        return {
            "keywords": list(self.keywords),
            "tools_mentioned": list(self.tools_mentioned),
            "file_types": list(self.file_types),
            "is_retry": self.is_retry,
            "is_verification": self.is_verification,
            "has_error_context": self.has_error_context,
        }


# Task type keywords mapping
TASK_KEYWORDS: Dict[TaskType, Set[str]] = {
    TaskType.RESEARCH: {
        "search", "find", "investigate", "research", "gather", "explore",
        "discover", "analyze", "check", "look", "query",
    },
    TaskType.ANALYSIS: {
        "analyze", "examine", "review", "audit", "assess", "evaluate",
        "inspect", "study", "parse", "understand",
    },
    TaskType.IMPLEMENTATION: {
        "implement", "create", "build", "write", "add", "modify",
        "change", "update", "fix", "patch", "develop", "code",
    },
    TaskType.VERIFICATION: {
        "verify", "test", "validate", "check", "confirm", "ensure",
        "assert", "prove", "run tests",
    },
    TaskType.DOCUMENTATION: {
        "document", "write docs", "readme", "explain", "describe",
        "annotate", "comment",
    },
    TaskType.PLANNING: {
        "plan", "design", "architect", "structure", "organize",
        "strategy", "approach",
    },
    TaskType.RECOVERY: {
        "fix", "repair", "recover", "retry", "debug", "resolve",
        "error", "failed", "broken",
    },
    TaskType.REVIEW: {
        "review", "check", "approve", "reject", "critique",
        "feedback", "opinion",
    },
    TaskType.COMMUNICATION: {
        "send", "notify", "message", "email", "post", "share",
        "communicate", "inform",
    },
}

# File type → role mapping
FILE_TYPE_ROLES: Dict[str, AgentRole] = {
    ".py": "executor",
    ".js": "executor",
    ".ts": "executor",
    ".go": "executor",
    ".rs": "executor",
    ".md": "reviewer",
    ".txt": "researcher",
    ".json": "researcher",
    ".yaml": "researcher",
    ".yml": "researcher",
    ".toml": "researcher",
    ".cfg": "researcher",
    ".ini": "researcher",
    ".test.": "reviewer",
    "_test.": "reviewer",
    "spec.": "reviewer",
}

# Tool → task type mapping
TOOL_TASK_TYPES: Dict[str, TaskType] = {
    "read": TaskType.RESEARCH,
    "glob": TaskType.RESEARCH,
    "grep": TaskType.RESEARCH,
    "web_search": TaskType.RESEARCH,
    "web_fetch": TaskType.RESEARCH,
    "write_file": TaskType.IMPLEMENTATION,
    "edit": TaskType.IMPLEMENTATION,
    "bash": TaskType.IMPLEMENTATION,
    "execute": TaskType.IMPLEMENTATION,
    "test": TaskType.VERIFICATION,
    "pytest": TaskType.VERIFICATION,
    "verify": TaskType.VERIFICATION,
}


@dataclass
class RoleAssignment:
    """Role assignment result."""
    role: AgentRole
    task_type: TaskType
    confidence: float
    reasoning: str
    alternatives: List[AgentRole] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict."""
        return {
            "role": self.role,
            "task_type": self.task_type.value,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "alternatives": self.alternatives,
        }


class RoleAssigner:
    """Dynamically assigns roles based on task analysis.
    
    Analyzes task description, tools, and context to infer
    the most appropriate agent role.
    
    Usage:
        assigner = RoleAssigner()
        
        signature = assigner.extract_signature(task_description, tools)
        assignment = assigner.assign_role(signature)
        
        role = assignment.role
    """
    
    def __init__(self) -> None:
        """Initialize role assigner."""
        self.assignment_history: List[RoleAssignment] = []
    
    def extract_signature(
        self,
        description: str,
        tools: List[str] = None,
        files: List[str] = None,
        is_retry: bool = False,
        is_verification: bool = False,
        has_error_context: bool = False,
    ) -> TaskSignature:
        """Extract task signature from description.
        
        Args:
            description: Task description
            tools: Tools mentioned (optional)
            files: Files involved (optional)
            is_retry: Is this a retry
            is_verification: Is verification task
            has_error_context: Has error context
            
        Returns:
            TaskSignature
        """
        # Extract keywords
        keywords = set()
        desc_lower = description.lower()
        
        for word in desc_lower.split():
            # Remove punctuation
            clean_word = word.strip(".,!?;:()[]{}")
            if len(clean_word) > 2:
                keywords.add(clean_word)
        
        # Extract tools
        tools_mentioned = set(tools or [])
        
        # Also check for tool names in description
        for tool in TOOL_TASK_TYPES:
            if tool in desc_lower:
                tools_mentioned.add(tool)
        
        # Extract file types
        file_types = set()
        for file in (files or []):
            for ext in FILE_TYPE_ROLES:
                if file.endswith(ext) or ext in file:
                    file_types.add(ext)
        
        return TaskSignature(
            keywords=keywords,
            tools_mentioned=tools_mentioned,
            file_types=file_types,
            is_retry=is_retry,
            is_verification=is_verification,
            has_error_context=has_error_context,
        )
    
    def classify_task_type(self, signature: TaskSignature) -> TaskType:
        """Classify task type from signature.
        
        Args:
            signature: TaskSignature
            
        Returns:
            TaskType
        """
        scores: Dict[TaskType, int] = {}
        
        # Score based on keywords
        for task_type, keywords in TASK_KEYWORDS.items():
            overlap = len(signature.keywords & keywords)
            scores[task_type] = overlap
        
        # Score based on tools
        for tool in signature.tools_mentioned:
            task_type = TOOL_TASK_TYPES.get(tool)
            if task_type:
                scores[task_type] = scores.get(task_type, 0) + 2
        
        # Special cases
        if signature.is_retry or signature.has_error_context:
            scores[TaskType.RECOVERY] = scores.get(TaskType.RECOVERY, 0) + 5
        
        if signature.is_verification:
            scores[TaskType.VERIFICATION] = scores.get(TaskType.VERIFICATION, 0) + 10
        
        # Find highest score
        if not scores:
            return TaskType.IMPLEMENTATION  # Default
        
        max_type = max(scores, key=scores.get)
        
        return max_type
    
    def infer_role_from_task_type(self, task_type: TaskType) -> AgentRole:
        """Infer role from task type.
        
        Args:
            task_type: TaskType
            
        Returns:
            AgentRole
        """
        ROLE_MAPPING: Dict[TaskType, AgentRole] = {
            TaskType.RESEARCH: "researcher",
            TaskType.ANALYSIS: "researcher",
            TaskType.IMPLEMENTATION: "executor",
            TaskType.VERIFICATION: "reviewer",
            TaskType.DOCUMENTATION: "executor",
            TaskType.PLANNING: "planner",
            TaskType.RECOVERY: "recovery",
            TaskType.REVIEW: "reviewer",
            TaskType.COMMUNICATION: "executor",
        }
        
        return ROLE_MAPPING.get(task_type, "executor")
    
    def infer_role_from_files(self, signature: TaskSignature) -> Optional[AgentRole]:
        """Infer role from file types.
        
        Args:
            signature: TaskSignature
            
        Returns:
            AgentRole or None
        """
        if not signature.file_types:
            return None
        
        # Get most specific file type role
        for file_type in signature.file_types:
            if file_type in FILE_TYPE_ROLES:
                return FILE_TYPE_ROLES[file_type]
        
        return None
    
    def assign_role(
        self,
        signature: TaskSignature,
        confidence_threshold: float = 0.5,
    ) -> RoleAssignment:
        """Assign role based on task signature.
        
        Combines multiple signals:
        - Keyword analysis
        - Tool usage
        - File types
        - Context flags
        
        Args:
            signature: TaskSignature
            confidence_threshold: Minimum confidence
            
        Returns:
            RoleAssignment
        """
        # Classify task type
        task_type = self.classify_task_type(signature)
        
        # Infer role from task type
        role = self.infer_role_from_task_type(task_type)
        
        # Override with file type if strong signal
        file_role = self.infer_role_from_files(signature)
        
        alternatives: List[AgentRole] = []
        
        if file_role and file_role != role:
            alternatives.append(file_role)
        
        # Calculate confidence
        confidence = self._calculate_confidence(signature, task_type)
        
        # Build reasoning
        reasoning = self._build_reasoning(signature, task_type, role)
        
        assignment = RoleAssignment(
            role=role,
            task_type=task_type,
            confidence=confidence,
            reasoning=reasoning,
            alternatives=alternatives,
        )
        
        # Record history
        self.assignment_history.append(assignment)
        
        return assignment
    
    def _calculate_confidence(
        self,
        signature: TaskSignature,
        task_type: TaskType,
    ) -> float:
        """Calculate confidence score.
        
        Args:
            signature: TaskSignature
            task_type: TaskType
            
        Returns:
            Confidence score (0-1)
        """
        # Base confidence
        confidence = 0.5
        
        # Boost for keyword overlap
        keyword_overlap = len(signature.keywords & TASK_KEYWORDS.get(task_type, set()))
        confidence += min(keyword_overlap * 0.1, 0.3)
        
        # Boost for tool match
        tool_match = any(
            TOOL_TASK_TYPES.get(t) == task_type
            for t in signature.tools_mentioned
        )
        if tool_match:
            confidence += 0.2
        
        # Boost for special flags
        if signature.is_verification and task_type == TaskType.VERIFICATION:
            confidence += 0.3
        
        if signature.is_retry and task_type == TaskType.RECOVERY:
            confidence += 0.3
        
        # Cap at 1.0
        return min(confidence, 1.0)
    
    def _build_reasoning(
        self,
        signature: TaskSignature,
        task_type: TaskType,
        role: AgentRole,
    ) -> str:
        """Build reasoning string.
        
        Args:
            signature: TaskSignature
            task_type: TaskType
            role: AgentRole
            
        Returns:
            Reasoning string
        """
        parts = []
        
        parts.append(f"Task classified as {task_type.value}")
        parts.append(f"→ assigned role {role}")
        
        if signature.keywords:
            matching_keywords = signature.keywords & TASK_KEYWORDS.get(task_type, set())
            if matching_keywords:
                parts.append(f"Keywords: {', '.join(list(matching_keywords)[:3])}")
        
        if signature.tools_mentioned:
            parts.append(f"Tools: {', '.join(signature.tools_mentioned)}")
        
        if signature.file_types:
            parts.append(f"Files: {', '.join(signature.file_types)}")
        
        if signature.is_retry:
            parts.append("(retry context)")
        
        if signature.is_verification:
            parts.append("(verification context)")
        
        return " | ".join(parts)
    
    def reassign_role(
        self,
        current_role: AgentRole,
        new_context: TaskSignature,
    ) -> Optional[RoleAssignment]:
        """Reassign role based on new context.
        
        Used when task context changes mid-execution.
        
        Args:
            current_role: Current role
            new_context: New TaskSignature
            
        Returns:
            New RoleAssignment if change needed, None otherwise
        """
        new_assignment = self.assign_role(new_context)
        
        if new_assignment.role != current_role:
            return new_assignment
        
        return None
    
    def get_role_statistics(self) -> Dict[str, Any]:
        """Get statistics on role assignments.
        
        Returns:
            Dict with statistics
        """
        if not self.assignment_history:
            return {"total_assignments": 0}
        
        role_counts: Dict[AgentRole, int] = {}
        task_type_counts: Dict[TaskType, int] = {}
        
        for assignment in self.assignment_history:
            role_counts[assignment.role] = role_counts.get(assignment.role, 0) + 1
            task_type_counts[assignment.task_type] = task_type_counts.get(assignment.task_type, 0) + 1
        
        return {
            "total_assignments": len(self.assignment_history),
            "role_distribution": role_counts,
            "task_type_distribution": {
                t.value: c for t, c in task_type_counts.items()
            },
            "average_confidence": sum(a.confidence for a in self.assignment_history) / len(self.assignment_history),
        }


def get_default_role_for_phase(phase: str) -> AgentRole:
    """Get default role for workflow phase.
    
    Args:
        phase: Phase name
        
    Returns:
        AgentRole
    """
    PHASE_ROLES: Dict[str, AgentRole] = {
        "research": "researcher",
        "synthesis": "planner",
        "implementation": "executor",
        "verification": "reviewer",
    }
    
    return PHASE_ROLES.get(phase, "executor")