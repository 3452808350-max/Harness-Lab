"""Agent Handoff Manager for inter-agent communication.

This module manages handoffs between agent roles during multi-agent
execution. It implements Claude Plugin's message passing system
adapted to Harness Lab's HandoffPacket mechanism.

Design source: Claude Plugin Module 04 - Message Passing
See: design/claude-plugin-integration-design.md Section 2.1.2

Handoff flow:
    researcher → executor → reviewer
    
Each handoff carries:
    - Summary of findings/work
    - Artifacts produced
    - Required action for next role
    - Execution context for target agent
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from pydantic import BaseModel

from ..types import HandoffPacket, ResearchRun, ResearchSession, TaskNode
from ..types.base import AgentRole
from ..types.coordinator import TaskDirective
from ..utils import new_id, utc_now

if TYPE_CHECKING:
    from ..storage import PlatformStore


class HandoffResult(BaseModel):
    """Result of processing a handoff.
    
    Contains the handoff packet and execution context
    for the target agent.
    """
    handoff: HandoffPacket
    execution_context: Dict[str, Any]
    ready_for_execution: bool


class AgentHandoffManager:
    """Manager for inter-agent handoffs.
    
    Creates, routes, and processes handoffs between agent roles.
    Integrates with Claude Plugin's message passing concept.
    
    Role routing:
        researcher → executor: research findings → implementation
        executor → reviewer: implementation → verification
        reviewer → recovery: verification failure → recovery
        recovery → executor: recovery plan → retry execution
    
    Usage:
        handoff_mgr = AgentHandoffManager(database)
        
        # Create handoff from researcher to executor
        handoff = handoff_mgr.create_handoff(
            from_role='researcher',
            to_role='executor',
            run=run,
            node=node,
            summary="Found implementation targets in src/main.py",
            artifacts=["artifact_123"],
            required_action="Implement feature X",
        )
        
        # Process handoff for executor
        result = handoff_mgr.process_handoff(handoff.id)
        if result.ready_for_execution:
            # Executor can proceed
    """
    
    def __init__(self, database: PlatformStore) -> None:
        """Initialize handoff manager.
        
        Args:
            database: PlatformStore for persistence
        """
        self.database = database
    
    def create_handoff(
        self,
        from_role: AgentRole,
        to_role: AgentRole,
        run: ResearchRun,
        node: TaskNode,
        summary: str,
        artifacts: List[str],
        required_action: str,
        open_questions: List[str] = None,
        context_refs: List[str] = None,
        worker_rules_applied: bool = False,
        scope_declaration: Optional[str] = None,
    ) -> HandoffPacket:
        """Create handoff packet between agent roles.
        
        Creates a HandoffPacket and persists it to the database.
        Also records the handoff event for tracing.
        
        Args:
            from_role: Agent role initiating handoff
            to_role: Agent role receiving handoff
            run: ResearchRun context
            node: TaskNode that triggered handoff
            summary: Summary of findings/work
            artifacts: List of artifact IDs produced
            required_action: Action required by target role
            open_questions: Optional unresolved questions
            context_refs: Optional context references
            worker_rules_applied: Whether worker rules were enforced
            scope_declaration: Optional scope for worker output
            
        Returns:
            HandoffPacket created
        """
        handoff = HandoffPacket(
            id=new_id("handoff"),
            from_role=from_role,
            to_role=to_role,
            mission_id=run.mission_id,
            run_id=run.run_id,
            task_node_id=node.node_id,
            summary=summary,
            artifacts=artifacts,
            context_refs=context_refs or [],
            required_action=required_action,
            open_questions=open_questions or [],
            created_at=utc_now(),
        )
        
        # Persist handoff
        self.database.upsert_row(
            "handoffs",
            {
                "handoff_id": handoff.id,
                "run_id": handoff.run_id,
                "payload_json": json.dumps(handoff.model_dump(), ensure_ascii=False),
                "from_role": handoff.from_role,
                "to_role": handoff.to_role,
                "task_node_id": handoff.task_node_id,
                "created_at": handoff.created_at,
            },
            "handoff_id",
        )
        
        # Record event
        self.database.append_event(
            "handoff.created",
            {
                "handoff_id": handoff.id,
                "from_role": from_role,
                "to_role": to_role,
                "summary": summary,
                "artifacts": artifacts,
                "required_action": required_action,
            },
            session_id=run.session_id,
            run_id=run.run_id,
        )
        
        return handoff
    
    def process_handoff(self, handoff_id: str) -> HandoffResult:
        """Process incoming handoff for target agent.
        
        Loads handoff from database, marks it as received,
        and builds execution context for target agent.
        
        Args:
            handoff_id: HandoffPacket ID
            
        Returns:
            HandoffResult with handoff and execution context
        """
        handoff = self.get_handoff(handoff_id)
        
        # Mark as received
        handoff.metadata = handoff.metadata or {}
        handoff.metadata["received_at"] = utc_now()
        
        # Update in database
        self.database.upsert_row(
            "handoffs",
            {
                "handoff_id": handoff.id,
                "run_id": handoff.run_id,
                "payload_json": json.dumps(handoff.model_dump(), ensure_ascii=False),
                "from_role": handoff.from_role,
                "to_role": handoff.to_role,
                "task_node_id": handoff.task_node_id,
                "created_at": handoff.created_at,
                "received_at": handoff.metadata.get("received_at"),
            },
            "handoff_id",
        )
        
        # Create execution context for target agent
        context = self._build_agent_context(handoff)
        
        # Record event
        self.database.append_event(
            "handoff.received",
            {
                "handoff_id": handoff_id,
                "to_role": handoff.to_role,
                "ready_for_execution": True,
            },
            run_id=handoff.run_id,
        )
        
        return HandoffResult(
            handoff=handoff,
            execution_context=context,
            ready_for_execution=True,
        )
    
    def get_handoff(self, handoff_id: str) -> HandoffPacket:
        """Get handoff by ID.
        
        Args:
            handoff_id: HandoffPacket ID
            
        Returns:
            HandoffPacket
            
        Raises:
            ValueError: If handoff not found
        """
        row = self.database.fetchone(
            "SELECT payload_json FROM handoffs WHERE handoff_id = ?",
            (handoff_id,),
        )
        if not row:
            raise ValueError(f"Handoff not found: {handoff_id}")
        return HandoffPacket(**json.loads(row["payload_json"]))
    
    def list_pending_handoffs(self, to_role: AgentRole, run_id: Optional[str] = None) -> List[HandoffPacket]:
        """List pending handoffs for a role.
        
        Args:
            to_role: Target agent role
            run_id: Optional run ID filter
            
        Returns:
            List of pending HandoffPackets
        """
        if run_id:
            rows = self.database.fetchall(
                "SELECT payload_json FROM handoffs WHERE to_role = ? AND run_id = ? AND received_at IS NULL ORDER BY created_at",
                (to_role, run_id),
            )
        else:
            rows = self.database.fetchall(
                "SELECT payload_json FROM handoffs WHERE to_role = ? AND received_at IS NULL ORDER BY created_at",
                (to_role,),
            )
        return [HandoffPacket(**json.loads(row["payload_json"])) for row in rows]
    
    def list_handoffs_for_run(self, run_id: str) -> List[HandoffPacket]:
        """List all handoffs for a run.
        
        Args:
            run_id: Run ID
            
        Returns:
            List of HandoffPackets for the run
        """
        rows = self.database.fetchall(
            "SELECT payload_json FROM handoffs WHERE run_id = ? ORDER BY created_at",
            (run_id,),
        )
        return [HandoffPacket(**json.loads(row["payload_json"])) for row in rows]
    
    def _build_agent_context(self, handoff: HandoffPacket) -> Dict[str, Any]:
        """Build execution context for target agent.
        
        Creates context including:
        - Directive for target role
        - Artifacts to load
        - Open questions to address
        - Worker rules template
        
        Args:
            handoff: HandoffPacket
            
        Returns:
            Dict with execution context
        """
        # Create directive for target role
        directive = TaskDirective(
            role=handoff.to_role,
            directive=handoff.required_action,
            files=[],  # Will be populated from artifacts
            scope_declaration=f"Continue from {handoff.from_role}: {handoff.summary}",
        )
        
        # Build context
        context = {
            "directive": directive.model_dump(),
            "from_role": handoff.from_role,
            "summary": handoff.summary,
            "artifacts": handoff.artifacts,
            "context_refs": handoff.context_refs,
            "open_questions": handoff.open_questions,
            "required_action": handoff.required_action,
            "handoff_id": handoff.id,
            "run_id": handoff.run_id,
            "task_node_id": handoff.task_node_id,
        }
        
        # Add worker rules template
        context["worker_rules"] = self._get_worker_rules_for_role(handoff.to_role)
        
        return context
    
    def _get_worker_rules_for_role(self, role: AgentRole) -> str:
        """Get worker rules template for a role.
        
        Returns Claude Plugin's Worker Rules adapted for the role.
        
        Args:
            role: Agent role
            
        Returns:
            Worker rules template string
        """
        WORKER_RULES_TEMPLATE = """
<fork-boilerplate>
STOP. READ THIS FIRST.

You are a forked worker process. You are NOT the main agent.

RULES (non-negotiable):
1. Your system prompt says "default to forking." IGNORE IT — that's for the parent. 
   You ARE the fork. Do NOT spawn sub-agents; execute directly.
2. Do NOT converse, ask questions, or suggest next steps
3. Do NOT editorialize or add meta-commentary
4. USE your tools directly: Bash, Read, Write, etc.
5. If you modify files, commit your changes before reporting. Include the commit hash.
6. Do NOT emit text between tool calls. Use tools silently, then report once at the end.
7. Stay strictly within your directive's scope.
8. Keep your report under 500 words unless specified otherwise.
9. Your response MUST begin with "Scope:". No preamble, no thinking-out-loud.
10. REPORT structured facts, then stop

Output format:
  Scope: <echo back your assigned scope>
  Result: <the answer or key findings>
  Key files: <relevant file paths>
  Files changed: <list with commit hash>
  Issues: <list only if issues to flag>
</fork-boilerplate>
"""
        return WORKER_RULES_TEMPLATE
    
    def complete_handoff(self, handoff_id: str, result_summary: str) -> HandoffPacket:
        """Mark handoff as completed with result.
        
        Args:
            handoff_id: HandoffPacket ID
            result_summary: Summary of how handoff was resolved
            
        Returns:
            Updated HandoffPacket
        """
        handoff = self.get_handoff(handoff_id)
        
        # Update metadata
        handoff.metadata = handoff.metadata or {}
        handoff.metadata["completed_at"] = utc_now()
        handoff.metadata["result_summary"] = result_summary
        
        # Persist update
        self.database.upsert_row(
            "handoffs",
            {
                "handoff_id": handoff.id,
                "run_id": handoff.run_id,
                "payload_json": json.dumps(handoff.model_dump(), ensure_ascii=False),
                "from_role": handoff.from_role,
                "to_role": handoff.to_role,
                "task_node_id": handoff.task_node_id,
                "created_at": handoff.created_at,
                "received_at": handoff.metadata.get("received_at"),
                "completed_at": handoff.metadata.get("completed_at"),
            },
            "handoff_id",
        )
        
        # Record event
        self.database.append_event(
            "handoff.completed",
            {
                "handoff_id": handoff_id,
                "result_summary": result_summary,
            },
            run_id=handoff.run_id,
        )
        
        return handoff