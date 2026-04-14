"""Parallel Agent Coordinator for multi-agent orchestration.

This module implements Claude Plugin's four-phase workflow:
    Phase 1: Research - parallel investigation workers
    Phase 2: Synthesis - Coordinator processes findings (no workers)
    Phase 3: Implementation - parallel execution workers (file-grouped)
    Phase 4: Verification - parallel validation workers

Design source: Claude Plugin Module 05 - Coordinator Mode
See: design/claude-plugin-integration-design.md Section 2.1.1

Key features:
    - Parallel worker dispatch with max_parallel limit
    - File-grouped implementation (same file → sequential)
    - Continue vs Spawn decision integration
    - Token budget allocation across phases
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..types import ResearchRun, ResearchSession, TaskNode, TaskGraph
from ..types.base import AgentRole
from ..types.coordinator import (
    CoordinatorConfig,
    CoordinatorState,
    WorkflowPhase,
    WorkerResult,
    WorkerInfo,
    TaskDirective,
    ResearchDirection,
    ImplementationGroup,
)
from ..types.decision import TaskContext, ContinueVsSpawnDecision
from ..utils import new_id, utc_now
from .decision_engine import ContinueSpawnDecisionEngine

if TYPE_CHECKING:
    from ..storage import PlatformStore
    from ..fleet.dispatcher import Dispatcher
    from .service import OrchestratorService


class ParallelAgentCoordinator:
    """Multi-agent coordinator implementing four-phase workflow.
    
    Orchestrates parallel worker execution across research,
    implementation, and verification phases.
    
    Usage:
        coordinator = ParallelAgentCoordinator(
            orchestrator=orchestrator,
            dispatcher=dispatcher,
            database=database,
            config=CoordinatorConfig(max_parallel_workers=4),
        )
        
        results = await coordinator.run_workflow(session, run)
    """
    
    PHASE_SEQUENCE = ["research", "synthesis", "implementation", "verification"]
    
    def __init__(
        self,
        orchestrator: OrchestratorService,
        dispatcher: Dispatcher,
        database: PlatformStore,
        config: CoordinatorConfig = None,
    ) -> None:
        """Initialize coordinator.
        
        Args:
            orchestrator: OrchestratorService for task graph management
            dispatcher: Dispatcher for worker dispatch
            database: PlatformStore for persistence
            config: CoordinatorConfig (optional, defaults provided)
        """
        self.orchestrator = orchestrator
        self.dispatcher = dispatcher
        self.database = database
        self.config = config or CoordinatorConfig()
        self.decision_engine = ContinueSpawnDecisionEngine()
        self.state: Optional[CoordinatorState] = None
    
    def run_workflow(self, session: ResearchSession, run: ResearchRun) -> List[WorkerResult]:
        """Execute four-phase workflow.
        
        Synchronous wrapper for async workflow execution.
        
        Args:
            session: ResearchSession context
            run: ResearchRun to execute
            
        Returns:
            List of WorkerResult from all phases
        """
        import asyncio
        try:
            # Try to get existing event loop
            loop = asyncio.get_running_loop()
            # If we're in an async context, create task
            return asyncio.ensure_future(self._run_workflow_async(session, run))
        except RuntimeError:
            # No running loop, create new one
            return asyncio.run(self._run_workflow_async(session, run))
    
    async def _run_workflow_async(
        self,
        session: ResearchSession,
        run: ResearchRun,
    ) -> List[WorkerResult]:
        """Execute four-phase workflow asynchronously.
        
        Phase sequence:
        1. Research - spawn parallel research workers
        2. Synthesis - coordinator processes findings (no workers)
        3. Implementation - parallel execution grouped by file
        4. Verification - parallel validation workers
        
        Args:
            session: ResearchSession context
            run: ResearchRun to execute
            
        Returns:
            List of WorkerResult from all phases
        """
        # Initialize state
        self.state = CoordinatorState(
            session_id=session.session_id,
            run_id=run.run_id,
            current_phase=WorkflowPhase(name="research"),
        )
        
        all_results: List[WorkerResult] = []
        
        # Phase 1: Research
        self.state.current_phase.start()
        self._record_phase_event("phase.started", "research")
        
        research_results = await self._run_research_phase(session, run)
        all_results.extend(research_results)
        
        self.state.current_phase.complete()
        self._record_phase_event("phase.completed", "research", len(research_results))
        
        # Phase 2: Synthesis (Coordinator handles, no workers)
        self.state.current_phase = WorkflowPhase(name="synthesis")
        self.state.current_phase.start()
        self._record_phase_event("phase.started", "synthesis")
        
        synthesis = self._synthesize_findings(research_results, session)
        self.state.synthesis_output = synthesis.get("summary")
        self.state.implementation_plan = synthesis.get("plan")
        
        self.state.current_phase.complete()
        self._record_phase_event("phase.completed", "synthesis")
        
        # Phase 3: Implementation
        self.state.current_phase = WorkflowPhase(name="implementation")
        self.state.current_phase.start()
        self._record_phase_event("phase.started", "implementation")
        
        impl_results = await self._run_implementation_phase(session, run, synthesis)
        all_results.extend(impl_results)
        
        self.state.current_phase.complete()
        self._record_phase_event("phase.completed", "implementation", len(impl_results))
        
        # Phase 4: Verification (if enabled)
        if self.config.enable_verification:
            self.state.current_phase = WorkflowPhase(name="verification")
            self.state.current_phase.start()
            self._record_phase_event("phase.started", "verification")
            
            verify_results = await self._run_verification_phase(session, run)
            all_results.extend(verify_results)
            
            self.state.current_phase.complete()
            self._record_phase_event("phase.completed", "verification", len(verify_results))
        
        return all_results
    
    async def _run_research_phase(
        self,
        session: ResearchSession,
        run: ResearchRun,
    ) -> List[WorkerResult]:
        """Run research phase with parallel workers.
        
        Analyzes task for research directions, then spawns
        parallel workers to investigate each direction.
        
        Args:
            session: ResearchSession context
            run: ResearchRun
            
        Returns:
            List of WorkerResult from research workers
        """
        # Analyze research directions from session goal
        directions = self._analyze_research_directions(session)
        
        if not directions:
            return []
        
        # Create directives for each direction
        directives = [
            TaskDirective(
                role=direction.role,
                directive=direction.description,
                files=direction.files,
            )
            for direction in directions
        ]
        
        # Group into parallel batches
        batches = self._group_parallel_tasks(directives, max_parallel=self.config.max_parallel_workers)
        
        # Execute batches
        all_results: List[WorkerResult] = []
        
        for batch in batches:
            batch_results = await self._execute_batch(batch, session, run, phase="research")
            all_results.extend(batch_results)
            
            # Update state
            for result in batch_results:
                self.state.completed_results.append(result)
        
        return all_results
    
    def _analyze_research_directions(self, session: ResearchSession) -> List[ResearchDirection]:
        """Analyze session goal to determine research directions.
        
        Extracts research directions from:
        - Session goal and context
        - Intent declaration suggested action
        - Available files in workspace
        
        Args:
            session: ResearchSession
            
        Returns:
            List of ResearchDirection
        """
        directions: List[ResearchDirection] = []
        
        # Basic direction from session goal
        directions.append(
            ResearchDirection(
                direction_id=new_id("direction"),
                description=f"Research context for: {session.goal}",
                role="researcher",
                priority=100,
            )
        )
        
        # Direction from intent declaration if available
        if session.intent_declaration:
            action = session.intent_declaration.suggested_action
            directions.append(
                ResearchDirection(
                    direction_id=new_id("direction"),
                    description=f"Research tool requirements for: {action.tool_name}",
                    role="researcher",
                    files=list(action.payload.keys()) if action.payload else [],
                    priority=80,
                )
            )
        
        # Sort by priority
        directions.sort(key=lambda d: d.priority, reverse=True)
        
        return directions
    
    def _synthesize_findings(
        self,
        research_results: List[WorkerResult],
        session: ResearchSession,
    ) -> Dict[str, Any]:
        """Synthesize research findings into implementation plan.
        
        Coordinator phase - no workers, just synthesis logic.
        
        Args:
            research_results: Results from research workers
            session: ResearchSession
            
        Returns:
            Dict with synthesis summary and implementation plan
        """
        # Collect key findings
        key_files = []
        for result in research_results:
            key_files.extend(result.key_files)
        
        # Remove duplicates
        key_files = list(dict.fromkeys(key_files))
        
        # Build implementation plan
        plan = {
            "key_files": key_files,
            "research_summary": "\n".join([r.output for r in research_results if r.output]),
            "issues": [issue for r in research_results for issue in r.issues],
            "implementation_tasks": [],
        }
        
        # Extract implementation tasks from intent
        if session.intent_declaration:
            plan["implementation_tasks"].append({
                "tool_name": session.intent_declaration.suggested_action.tool_name,
                "priority": 100,
            })
        
        return {
            "summary": f"Synthesized {len(research_results)} research findings, identified {len(key_files)} key files",
            "plan": plan,
        }
    
    async def _run_implementation_phase(
        self,
        session: ResearchSession,
        run: ResearchRun,
        synthesis: Dict[str, Any],
    ) -> List[WorkerResult]:
        """Run implementation phase with parallel workers.
        
        Key rule: Same file → sequential execution
                 Different files → parallel OK
        
        Args:
            session: ResearchSession
            run: ResearchRun
            synthesis: Synthesis output from Phase 2
            
        Returns:
            List of WorkerResult from implementation workers
        """
        plan = synthesis.get("plan", {})
        
        # Group tasks by file
        groups = self._group_by_file(plan.get("key_files", []), session)
        
        # Create directives for each group
        directives = []
        for group in groups:
            directive = TaskDirective(
                role="executor",
                directive=f"Implement changes for files: {', '.join(group.files)}",
                files=group.files,
            )
            directives.append(directive)
            group.tasks = [directive]
        
        # Execute groups (respecting sequential constraint)
        all_results: List[WorkerResult] = []
        
        for group in groups:
            if group.sequential:
                # Same file group - execute one at a time
                for task in group.tasks:
                    result = await self._execute_single(task, session, run, phase="implementation")
                    all_results.append(result)
            else:
                # Different files - parallel OK
                batch_results = await self._execute_batch(
                    group.tasks, session, run, phase="implementation"
                )
                all_results.extend(batch_results)
        
        return all_results
    
    def _group_by_file(
        self,
        files: List[str],
        session: ResearchSession,
    ) -> List[ImplementationGroup]:
        """Group implementation tasks by file.
        
        Same file → sequential (one worker at a time)
        Different files → parallel OK
        
        Args:
            files: List of files
            session: ResearchSession
            
        Returns:
            List of ImplementationGroup
        """
        groups: List[ImplementationGroup] = []
        
        # Group files that appear together
        file_groups: Dict[str, List[str]] = {}
        
        for file in files:
            # Simple grouping: each file is its own group for now
            # In practice, could analyze dependencies
            key = file
            if key not in file_groups:
                file_groups[key] = []
            file_groups[key].append(file)
        
        # Create ImplementationGroup for each file group
        for group_id, group_files in enumerate(file_groups.values()):
            groups.append(
                ImplementationGroup(
                    group_id=f"group_{group_id}",
                    files=group_files,
                    sequential=len(group_files) == 1,  # Single file = sequential
                )
            )
        
        return groups
    
    async def _run_verification_phase(
        self,
        session: ResearchSession,
        run: ResearchRun,
    ) -> List[WorkerResult]:
        """Run verification phase with parallel workers.
        
        Fresh eyes rule: Verification should use new workers
        without implementation bias.
        
        Args:
            session: ResearchSession
            run: ResearchRun
            
        Returns:
            List of WorkerResult from verification workers
        """
        # Create verification directives
        directives = [
            TaskDirective(
                role="reviewer",
                directive="Verify main functionality works correctly",
                scope_declaration="Verification: check implementation correctness",
            ),
            TaskDirective(
                role="reviewer",
                directive="Check edge cases and error handling",
                scope_declaration="Verification: check edge cases",
            ),
        ]
        
        # Execute verification in parallel
        return await self._execute_batch(directives, session, run, phase="verification")
    
    async def _execute_batch(
        self,
        directives: List[TaskDirective],
        session: ResearchSession,
        run: ResearchRun,
        phase: str,
    ) -> List[WorkerResult]:
        """Execute a batch of directives in parallel.
        
        Creates workers for each directive and executes them
        concurrently, respecting max_parallel limit.
        
        Args:
            directives: List of TaskDirective
            session: ResearchSession
            run: ResearchRun
            phase: Current phase name
            
        Returns:
            List of WorkerResult
        """
        # Limit parallel workers
        batch_size = min(len(directives), self.config.max_parallel_workers)
        
        # Create workers
        worker_infos = []
        for i, directive in enumerate(directives[:batch_size]):
            worker_id = new_id("worker")
            worker_info = WorkerInfo(
                worker_id=worker_id,
                role=directive.role,
                directive=directive,
                status="running",
                started_at=utc_now(),
            )
            worker_infos.append(worker_info)
            if self.state:
                self.state.add_worker(worker_info)
        
        # Execute in parallel (simulated for now)
        # In production, would use dispatcher to dispatch to actual workers
        results = []
        for worker_info in worker_infos:
            result = await self._simulate_worker_execution(worker_info, session, run, phase)
            results.append(result)
            if self.state:
                self.state.complete_worker(worker_info.worker_id, result)
        
        return results
    
    async def _execute_single(
        self,
        directive: TaskDirective,
        session: ResearchSession,
        run: ResearchRun,
        phase: str,
    ) -> WorkerResult:
        """Execute a single directive.
        
        Args:
            directive: TaskDirective
            session: ResearchSession
            run: ResearchRun
            phase: Current phase name
            
        Returns:
            WorkerResult
        """
        worker_id = new_id("worker")
        worker_info = WorkerInfo(
            worker_id=worker_id,
            role=directive.role,
            directive=directive,
            status="running",
            started_at=utc_now(),
        )
        
        if self.state:
            self.state.add_worker(worker_info)
        
        result = await self._simulate_worker_execution(worker_info, session, run, phase)
        
        if self.state:
            self.state.complete_worker(worker_id, result)
        
        return result
    
    async def _simulate_worker_execution(
        self,
        worker_info: WorkerInfo,
        session: ResearchSession,
        run: ResearchRun,
        phase: str,
    ) -> WorkerResult:
        """Simulate worker execution for testing/development.
        
        In production, this would use dispatcher to dispatch
        to actual workers. For now, returns simulated results.
        
        Args:
            worker_info: WorkerInfo
            session: ResearchSession
            run: ResearchRun
            phase: Current phase name
            
        Returns:
            WorkerResult (simulated)
        """
        # Record worker start event
        self.database.append_event(
            f"{phase}.worker.started",
            {
                "worker_id": worker_info.worker_id,
                "role": worker_info.role,
                "directive": worker_info.directive.directive,
                "phase": phase,
            },
            session_id=session.session_id,
            run_id=run.run_id,
        )
        
        # Simulated execution (would be real worker execution in production)
        await asyncio.sleep(0.1)  # Simulate work
        
        result = WorkerResult(
            worker_id=worker_info.worker_id,
            success=True,
            output=f"Completed {phase} task: {worker_info.directive.directive}",
            scope=worker_info.directive.scope_declaration or worker_info.directive.directive,
            key_files=worker_info.directive.files,
            created_at=utc_now(),
        )
        
        # Record worker completion event
        self.database.append_event(
            f"{phase}.worker.completed",
            {
                "worker_id": worker_info.worker_id,
                "role": worker_info.role,
                "success": result.success,
                "output": result.output,
            },
            session_id=session.session_id,
            run_id=run.run_id,
        )
        
        return result
    
    def _group_parallel_tasks(
        self,
        directives: List[TaskDirective],
        max_parallel: int,
    ) -> List[List[TaskDirective]]:
        """Group directives into parallel batches.
        
        Args:
            directives: List of TaskDirective
            max_parallel: Maximum parallel workers
            
        Returns:
            List of batches (each batch is List[TaskDirective])
        """
        batches = []
        for i in range(0, len(directives), max_parallel):
            batch = directives[i:i + max_parallel]
            batches.append(batch)
        return batches
    
    def decide_continue_or_spawn(self, context: TaskContext) -> ContinueVsSpawnDecision:
        """Make Continue vs Spawn decision using decision engine.
        
        Args:
            context: TaskContext
            
        Returns:
            ContinueVsSpawnDecision
        """
        return self.decision_engine.decide(context)
    
    def _record_phase_event(self, event_type: str, phase: str, worker_count: int = 0) -> None:
        """Record phase transition event.
        
        Args:
            event_type: Event type (e.g., "phase.started")
            phase: Phase name
            worker_count: Number of workers (optional)
        """
        if self.state:
            self.database.append_event(
                event_type,
                {
                    "phase": phase,
                    "worker_count": worker_count,
                    "session_id": self.state.session_id,
                    "run_id": self.state.run_id,
                },
                session_id=self.state.session_id,
                run_id=self.state.run_id,
            )
    
    def get_phase_status(self) -> Optional[WorkflowPhase]:
        """Get current phase status.
        
        Returns:
            Current WorkflowPhase or None
        """
        if self.state:
            return self.state.current_phase
        return None
    
    def get_active_workers(self) -> List[WorkerInfo]:
        """Get list of active workers.
        
        Returns:
            List of WorkerInfo for running workers
        """
        if self.state:
            return [
                w for w in self.state.active_workers.values()
                if w.status == "running"
            ]
        return []