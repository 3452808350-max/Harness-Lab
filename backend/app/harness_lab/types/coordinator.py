"""Coordinator types for multi-agent orchestration.

Design source: Claude Plugin Module 05 - Coordinator Mode
See: design/claude-plugin-integration-design.md
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .base import AgentRole


class CoordinatorConfig(BaseModel):
    """Configuration for ParallelAgentCoordinator.
    
    Controls parallel execution behavior and resource limits.
    """
    max_parallel_workers: int = 4
    default_timeout_ms: int = 300000  # 5 minutes
    enable_verification: bool = True
    token_budget_allocation: Dict[str, float] = Field(
        default_factory=lambda: {
            "research": 0.25,
            "synthesis": 0.10,
            "implementation": 0.40,
            "verification": 0.25,
        }
    )


class WorkflowPhase(BaseModel):
    """Phase in the four-stage workflow.
    
    Phase sequence:
    1. Research - parallel investigation workers
    2. Synthesis - Coordinator processes findings (no workers)
    3. Implementation - parallel execution workers (file-grouped)
    4. Verification - parallel validation workers
    """
    name: str  # 'research' | 'synthesis' | 'implementation' | 'verification'
    status: str = "pending"  # 'pending' | 'running' | 'completed' | 'failed'
    workers: List[str] = Field(default_factory=list)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    
    def is_running(self) -> bool:
        return self.status == "running"
    
    def is_terminal(self) -> bool:
        return self.status in {"completed", "failed"}
    
    def start(self) -> None:
        """Mark phase as started."""
        self.status = "running"
        self.started_at = datetime.now().isoformat()
    
    def complete(self) -> None:
        """Mark phase as completed."""
        self.status = "completed"
        self.completed_at = datetime.now().isoformat()
    
    def fail(self) -> None:
        """Mark phase as failed."""
        self.status = "failed"
        self.completed_at = datetime.now().isoformat()


class WorkerResult(BaseModel):
    """Result from a worker execution.
    
    Follows Claude Plugin's Worker Rules output format:
    - Scope: assigned scope declaration
    - Result: key findings or output
    - Key files: relevant file paths
    - Files changed: modifications with commit hash
    - Issues: problems to flag
    """
    worker_id: str
    success: bool
    output: str
    scope: str
    key_files: List[str] = Field(default_factory=list)
    files_changed: List[Dict[str, str]] = Field(default_factory=list)
    issues: List[str] = Field(default_factory=list)
    usage: Dict[str, int] = Field(default_factory=dict)  # tokens, tool_uses, duration_ms
    created_at: Optional[str] = None
    
    def has_issues(self) -> bool:
        """Check if worker encountered issues."""
        return bool(self.issues)
    
    def has_file_changes(self) -> bool:
        """Check if worker modified files."""
        return bool(self.files_changed)


class TaskDirective(BaseModel):
    """Directive for a worker task.
    
    Contains the task specification and execution constraints.
    """
    role: AgentRole
    directive: str
    tools: List[str] = Field(default_factory=list)
    files: List[str] = Field(default_factory=list)
    max_turns: int = 100
    scope_declaration: Optional[str] = None
    
    def get_scope_declaration(self) -> str:
        """Get scope declaration for worker output format."""
        if self.scope_declaration:
            return self.scope_declaration
        return f"Investigate {self.directive}"


class WorkerInfo(BaseModel):
    """Information about an active worker."""
    worker_id: str
    role: AgentRole
    directive: TaskDirective
    status: str = "pending"  # 'pending' | 'running' | 'completed' | 'failed'
    started_at: Optional[str] = None
    result: Optional[WorkerResult] = None


class CoordinatorState(BaseModel):
    """State of the coordinator during execution."""
    session_id: str
    run_id: str
    current_phase: WorkflowPhase = Field(default_factory=lambda: WorkflowPhase(name="research"))
    active_workers: Dict[str, WorkerInfo] = Field(default_factory=dict)
    completed_results: List[WorkerResult] = Field(default_factory=list)
    synthesis_output: Optional[str] = None
    implementation_plan: Optional[Dict[str, Any]] = None
    
    def get_active_worker_count(self) -> int:
        """Get count of currently active workers."""
        return len([w for w in self.active_workers.values() if w.status == "running"])
    
    def get_completed_result_count(self) -> int:
        """Get count of completed worker results."""
        return len(self.completed_results)
    
    def add_worker(self, worker: WorkerInfo) -> None:
        """Add a worker to active workers."""
        self.active_workers[worker.worker_id] = worker
    
    def complete_worker(self, worker_id: str, result: WorkerResult) -> None:
        """Mark worker as completed with result."""
        if worker_id in self.active_workers:
            self.active_workers[worker_id].status = "completed"
            self.active_workers[worker_id].result = result
        self.completed_results.append(result)


class ResearchDirection(BaseModel):
    """A research direction for parallel investigation."""
    direction_id: str
    description: str
    role: AgentRole = "researcher"
    files: List[str] = Field(default_factory=list)
    priority: int = 0  # Higher priority = more important


class ImplementationGroup(BaseModel):
    """Group of implementation tasks by file.
    
    Same file → sequential (one worker at a time)
    Different files → parallel OK
    """
    group_id: str
    files: List[str] = Field(default_factory=list)
    tasks: List[TaskDirective] = Field(default_factory=list)
    sequential: bool = False  # True if same file group
    
    def get_parallel_count(self) -> int:
        """Get number of parallel tasks in group."""
        if self.sequential:
            return 1
        return len(self.tasks)