# Claude Plugin Multi-Agent Architecture Integration Design

## Executive Summary

This document outlines the integration strategy for Claude Plugin's multi-agent architecture into Harness Lab (code-flow). The goal is to transform Harness Lab from a single-worker execution model into a parallel multi-agent orchestration system.

**Key Benefits:**
- Parallel agent execution for faster task completion
- Four-phase workflow (research → synthesis → implementation → verification)
- Intelligent Continue vs Spawn decision engine
- Agent role specialization (planner/researcher/executor/reviewer)
- Token budget optimization via shared context caching

---

## Phase 1: Architecture Comparison Analysis

### 1.1 Claude Plugin Architecture Overview

Claude Plugin consists of 6 core modules:

| Module | Purpose | Key Concepts |
|--------|---------|--------------|
| Agent Definitions | Type system for agents | Frontmatter parsing, tool filtering, built-in agents |
| Fork Subagent | Parallel execution | Worker Rules, prompt cache, recursive protection |
| Task Notification | Completion protocol | TaskRegistry, task lifecycle events |
| Message Passing | Inter-agent communication | AgentRegistry, TeamManager, MessageRouter |
| Coordinator Mode | Workflow orchestration | Four-phase workflow, wave scheduling |
| Permission System | Access control | bubble/default modes, tool filtering |

#### 1.1.1 Agent Definitions (Module 01)

```typescript
interface AgentDefinition {
  agentType: string;       // 'fork', 'explore', 'custom'
  name: string;            // Display name
  description?: string;
  model: string;           // 'inherit' or specific model
  maxTurns: number;        // Turn limit
  permissionMode: 'bubble' | 'default';
  tools: {
    allow: string[];       // ['*'] or specific tools
    deny: string[];        // Forbidden tools
  };
  oneShot?: boolean;       // Single execution mode
  skills?: string[];       // Required skills
  systemPrompt?: string;
  source: 'built-in' | 'user';
}
```

**Built-in Agents:**
- `fork`: Lightweight parallel execution
- `explore`: Codebase exploration
- `edit`: File editing specialist
- `reasoning`: Complex analysis

#### 1.1.2 Fork Subagent (Module 02)

The Fork mechanism enables lightweight parallel execution:

**Worker Rules (10 Iron Laws):**
```
1. NOT the main agent - execute directly, don't spawn sub-agents
2. Do NOT converse, ask questions, or suggest next steps
3. Do NOT editorialize or add meta-commentary
4. USE tools directly: Bash, Read, Write, etc.
5. Commit changes before reporting, include commit hash
6. Do NOT emit text between tool calls
7. Stay strictly within directive's scope
8. Keep report under 500 words
9. Response MUST begin with "Scope:"
10. REPORT structured facts, then stop
```

**Output Format:**
```
Scope: <assigned scope>
Result: <answer or key findings>
Key files: <relevant file paths>
Files changed: <list with commit hash>
Issues: <list only if issues to flag>
```

**Prompt Cache Optimization:**
- Forked messages reuse parent's tool_use blocks
- Unified placeholder result reduces token burn
- Cache-friendly message structure

#### 1.1.3 Coordinator Mode (Module 05)

Four-phase workflow:

```
Phase 1: Research
├─ Analyze task and determine research directions
├─ Spawn parallel research workers
├─ Wait for all workers to complete
└─ Collect results

Phase 2: Synthesis
├─ Coordinator synthesizes findings (no workers)
└─ Create implementation plan

Phase 3: Implementation
├─ Group tasks by file
├─ Same file → sequential (one worker at a time)
├─ Different files → parallel OK
└─ Execute implementation workers

Phase 4: Verification
├─ Spawn verification workers
├─ Verify main functionality works
├─ Check edge cases
└─ Run tests
```

**Worker Types:**
- `research`: Investigation and information gathering
- `implementation`: Code changes and execution
- `verification`: Testing and validation
- `custom`: Specialized tasks

**Continue vs Spawn Decision Engine:**

| Scenario | Decision | Reason |
|----------|----------|--------|
| Worker has files in context | Continue | Avoid re-loading context |
| Broad research, narrow implementation | Spawn | Clean focused context |
| Correcting failed attempt | Continue | Error context useful |
| Verifying code just written | Spawn | Fresh eyes needed |
| Wrong approach retry | Spawn | Wrong context pollutes |

#### 1.1.4 Task Notification (Module 03)

Task lifecycle events:
```
register → running → completed | failed | killed
```

```typescript
interface TaskNotification {
  taskId: string;
  status: 'registered' | 'running' | 'completed' | 'failed' | 'killed';
  result?: string;
  usage?: {
    totalTokens: number;
    toolUses: number;
    durationMs: number;
  };
}
```

#### 1.1.5 Message Passing (Module 04)

**Agent Registry:**
```typescript
class AgentRegistry {
  register(id: string, name: string): void;
  setStatus(id: string, status: 'idle' | 'running' | 'completed' | 'failed'): void;
  getAgent(id: string): AgentInfo;
  listAgents(): AgentInfo[];
}
```

**Team Manager:**
```typescript
class TeamManager {
  createTeam(name: string, members: string[]): Team;
  deleteTeam(teamId: string): void;
  addMember(teamId: string, agentId: string): void;
  removeMember(teamId: string, agentId: string): void;
}
```

**Message Router:**
```typescript
class MessageRouter {
  route(message: AgentMessage): void;
  broadcast(teamId: string, message: AgentMessage): void;
}
```

#### 1.1.6 Permission System (Module 06)

**Permission Modes:**

| Mode | Description |
|------|-------------|
| `bubble` | Errors bubble up to parent context |
| `default` | Standard permission flow |

**Tool Filtering:**
```typescript
const INTERNAL_WORKER_TOOLS = [
  'TeamCreate', 'TeamDelete', 'SendMessage', 'SyntheticOutput'
];

function filterToolsForWorker(tools: string[], mode: 'simple' | 'full'): string[] {
  if (mode === 'simple') return tools.filter(t => WORKER_TOOLS_SIMPLE.includes(t));
  return tools.filter(t => !INTERNAL_WORKER_TOOLS.includes(t));
}
```

---

### 1.2 Harness Lab Architecture Overview

#### 1.2.1 Orchestrator Service

**Current Implementation:**
- DAG-native orchestrator with wave scheduling
- `next_wave()` returns ready nodes based on dependency satisfaction
- `mark_node_status()` updates node state
- `skip_unreachable_nodes()` handles failed upstream nodes
- `is_terminal()` checks completion status

**Task Graph Structure:**
```python
class TaskNode(BaseModel):
    node_id: str
    label: str
    kind: str              # 'intent', 'context', 'prompt', 'policy', 'execution', 'verification', 'learning'
    role: str              # 'executor', 'planner', 'reviewer'
    agent_role: AgentRole  # Maps to Claude Plugin worker types
    status: str            # 'planned', 'ready', 'leased', 'running', 'completed', 'failed', 'skipped', 'blocked'
    dependencies: List[str]
    metadata: Dict[str, Any]

class TaskEdge(BaseModel):
    edge_id: str
    source: str
    target: str
    kind: str              # 'depends_on', 'handoff', 'on_failure'

class TaskGraph(BaseModel):
    task_graph_id: str
    nodes: List[TaskNode]
    edges: List[TaskEdge]
    execution_strategy: str  # 'single_worker_wave_ready' | 'multi_agent_wave_ready'
```

**Key Gap:**
- Currently uses `execution_strategy="single_worker_wave_ready"`
- Has `multi_agent_wave_ready` defined but not implemented
- `agent_role` field exists but not utilized

#### 1.2.2 Runtime Service

**Execution Pipeline:**
```
Session → Run → TaskGraph → Wave → Lease → Worker → Execution → Completion → AfterTransition
```

**RunCoordinator Methods:**
- `coordination_snapshot()`: Current run state
- `mark_ready_nodes()`: Transition planned → ready
- `after_lease_transition()`: Post-execution handling
- `timeline_summary()`: Execution history

**LocalWorkerAdapter:**
- `drain_run()`: Execute until terminal state
- `execute_leased_task()`: Single task execution

**Gap:**
- Single worker execution (`drain_run` loops)
- No parallel task dispatch
- No agent role specialization

#### 1.2.3 Dispatcher

**Current Implementation:**
- Queue-based ready task dispatch
- Worker constraint matching
- Lease creation and management
- Stale lease reclamation

**Queue Sharding:**
```
Format: "{role}/{risk_level}/{label1}/{label2}/..."
```

**Key Features:**
- `_worker_can_poll_shard()`: Role and label matching
- `next_dispatch_for_worker()`: Primary dispatch entry
- `_create_dispatch()`: Transactional lease creation

---

### 1.3 Architecture Mapping

| Claude Plugin Concept | Harness Lab Equivalent | Integration Strategy |
|----------------------|------------------------|---------------------|
| Coordinator Mode | OrchestratorService | Extend with wave scheduling |
| Fork Subagent | Dispatcher + Fleet | Parallel dispatch with role filtering |
| Worker Rules | TaskNode metadata | Embed rules in execution context |
| Task Notification | Event System | Map to `database.append_event` |
| Message Passing | HandoffPacket | Extend handoff mechanism |
| Agent Registry | WorkerRegistry | Add agent_type metadata |
| Continue vs Spawn | Decision Engine | New module: decision_engine.py |
| Worker Types | AgentRole | Map to existing `AgentRole` literal |

---

## Phase 2: Integration Design

### 2.1 Core Component Design

#### 2.1.1 ParallelAgentCoordinator (orchestrator/coordinator.py)

```python
class ParallelAgentCoordinator:
    """Multi-agent coordinator integrating Claude Plugin workflow."""
    
    def __init__(
        self,
        orchestrator: OrchestratorService,
        dispatcher: Dispatcher,
        max_parallel_workers: int = 4,
        default_timeout_ms: int = 300000,
        enable_verification: bool = True,
    ):
        self.orchestrator = orchestrator
        self.dispatcher = dispatcher
        self.config = CoordinatorConfig(
            max_parallel_workers=max_parallel_workers,
            default_timeout_ms=default_timeout_ms,
            enable_verification=enable_verification,
        )
        self.phase: WorkflowPhase = 'research'
        self.active_workers: Dict[str, WorkerInfo] = {}
    
    def run_workflow(self, session: ResearchSession, run: ResearchRun) -> List[WorkerResult]:
        """Execute four-phase workflow."""
        # Phase 1: Research
        research_results = self._run_research_phase(session, run)
        
        # Phase 2: Synthesis (Coordinator handles, no workers)
        synthesis = self._synthesize_findings(research_results)
        
        # Phase 3: Implementation
        impl_results = self._run_implementation_phase(session, run, synthesis)
        
        # Phase 4: Verification (if enabled)
        if self.config.enable_verification:
            verify_results = self._run_verification_phase(session, run)
        
        return self._finalize_results(...)
    
    def _run_research_phase(self, session, run) -> List[WorkerResult]:
        """Spawn parallel research workers."""
        # Analyze task for research directions
        directions = self._analyze_research_directions(session)
        
        # Group into parallel batches
        batches = self._group_parallel_tasks(directions)
        
        # Dispatch parallel workers
        for batch in batches:
            self._dispatch_parallel_workers(batch, session, run)
        
        # Wait for completion
        return self._await_worker_results(...)
    
    def _dispatch_parallel_workers(
        self,
        tasks: List[TaskDirective],
        session: ResearchSession,
        run: ResearchRun,
    ) -> List[str]:
        """Dispatch multiple workers in parallel."""
        worker_ids = []
        for task in tasks:
            # Create worker with role
            worker = self._create_worker(
                role=task.role,
                directive=task.directive,
                tools=task.tools,
            )
            # Dispatch via existing dispatcher
            dispatch = self.dispatcher.next_dispatch_for_worker(...)
            worker_ids.append(worker.worker_id)
        return worker_ids
    
    def decide_continue_or_spawn(self, context: TaskContext) -> ContinueVsSpawnDecision:
        """Implement Claude Plugin's decision engine."""
        # Scene 1: Worker has files in context
        if self._worker_has_context(context):
            return ContinueVsSpawnDecision(
                action='continue',
                reason='Worker already has files in context'
            )
        
        # Scene 2: Broad research, narrow implementation
        if context.research_scope == 'broad' and context.impl_scope == 'narrow':
            return ContinueVsSpawnDecision(
                action='spawn',
                reason='Avoid exploration noise, focused context is cleaner'
            )
        
        # ... other scenes
        
        return ContinueVsSpawnDecision(action='spawn', reason='No useful context to reuse')
```

#### 2.1.2 AgentHandoffManager (runtime/handoff.py)

```python
class AgentHandoffManager:
    """Manage inter-agent handoffs with Claude Plugin message passing."""
    
    def __init__(self, database: PlatformStore, message_router: MessageRouter):
        self.database = database
        self.message_router = message_router
    
    def create_handoff(
        self,
        from_role: AgentRole,
        to_role: AgentRole,
        run: ResearchRun,
        node: TaskNode,
        summary: str,
        artifacts: List[str],
        required_action: str,
    ) -> HandoffPacket:
        """Create handoff packet between agent roles."""
        handoff = HandoffPacket(
            id=new_id("handoff"),
            from_role=from_role,
            to_role=to_role,
            run_id=run.run_id,
            task_node_id=node.node_id,
            summary=summary,
            artifacts=artifacts,
            required_action=required_action,
            created_at=utc_now(),
        )
        self.database.upsert_handoff(handoff)
        
        # Route message via Claude Plugin's MessageRouter
        self.message_router.route(
            AgentMessage(
                from_agent=from_role,
                to_agent=to_role,
                content=handoff.summary,
                metadata={'handoff_id': handoff.id}
            )
        )
        
        return handoff
    
    def process_handoff(self, handoff_id: str) -> HandoffResult:
        """Process incoming handoff for target agent."""
        handoff = self.database.get_handoff(handoff_id)
        
        # Mark as received
        handoff.metadata['received_at'] = utc_now()
        
        # Create execution context for target agent
        context = self._build_agent_context(handoff)
        
        return HandoffResult(
            handoff=handoff,
            execution_context=context,
            ready_for_execution=True,
        )
```

#### 2.1.3 ContinueSpawnDecisionEngine (orchestrator/decision_engine.py)

```python
class ContinueSpawnDecisionEngine:
    """Implement Claude Plugin's Continue vs Spawn decision logic."""
    
    # Decision scenes from Claude Plugin Module 05
    SCENES = [
        # Scene 1: Worker has files in context
        lambda ctx: (
            'continue' if ctx.worker_has_research_files_in_target()
            else None
        ),
        
        # Scene 2: Broad research, narrow implementation
        lambda ctx: (
            'spawn' if ctx.research_scope == 'broad' and ctx.impl_scope == 'narrow'
            else None
        ),
        
        # Scene 3: Correcting failed attempt
        lambda ctx: (
            'continue' if ctx.is_retry and ctx.previous_attempt_failed
            else None
        ),
        
        # Scene 4: Verifying code just written
        lambda ctx: (
            'spawn' if ctx.is_verification and ctx.target_worker_just_wrote_code
            else None
        ),
        
        # Scene 5: Wrong approach retry
        lambda ctx: (
            'spawn' if ctx.is_retry and ctx.previous_approach_was_wrong
            else None
        ),
    ]
    
    def decide(self, context: TaskContext) -> ContinueVsSpawnDecision:
        """Evaluate scenes in order, return first match."""
        for scene in self.SCENES:
            action = scene(context)
            if action:
                return ContinueVsSpawnDecision(
                    action=action,
                    reason=self._get_reason(action, context),
                )
        
        # Default: spawn fresh
        return ContinueVsSpawnDecision(
            action='spawn',
            reason='No useful context to reuse'
        )
    
    def _get_reason(self, action: str, context: TaskContext) -> str:
        """Generate human-readable reason."""
        reasons = {
            ('continue', 'files_in_context'): 'Worker already has files in context',
            ('spawn', 'scope_mismatch'): 'Avoid exploration noise, focused context is cleaner',
            ('continue', 'error_context'): 'Worker has error context',
            ('spawn', 'fresh_eyes'): 'Verifier should have fresh eyes',
            ('spawn', 'wrong_approach'): 'Wrong-approach context pollutes retry',
        }
        # ... matching logic
```

---

### 2.2 Data Flow Design

#### 2.2.1 Complete Execution Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Session Creation                              │
│  SessionRequest → ResearchSession (execution_mode="multi_agent")    │
└─────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      Intent Declaration                              │
│  goal → IntentDeclaration → suggested_action → TaskGraph            │
└─────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    ParallelAgentCoordinator                          │
│  Phase 1: Research                                                   │
│  ├─ analyze_research_directions() → [directive1, directive2, ...]   │
│  ├─ group_parallel_tasks() → parallel batches                        │
│  ├─ dispatch_parallel_workers() → WorkerFleet                        │
│  └─ await_worker_results() → research_results                       │
└─────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      Synthesis (Coordinator)                         │
│  synthesize_findings(research_results) → implementation_plan        │
│  (No workers - Coordinator handles this phase)                       │
└─────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Phase 3: Implementation                                             │
│  ├─ group_tasks_by_file() → file_groups                              │
│  │   Same file → sequential (same worker)                           │
│  │   Different files → parallel                                      │
│  ├─ ContinueSpawnDecisionEngine.decide() → continue | spawn          │
│  ├─ dispatch_implementation_workers() → execution                   │
│  └─ record_handoffs() → AgentHandoffManager                         │
└─────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Phase 4: Verification                                               │
│  ├─ spawn_verification_workers() → parallel verification            │
│  ├─ verify_main_functionality                                        │
│  ├─ check_edge_cases                                                 │
│  └─ run_tests                                                        │
└─────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         Finalization                                 │
│  finalize_results() → Run.result                                     │
│  persist_replay() → ExecutionTrace                                   │
│  Run.status = "completed"                                            │
└─────────────────────────────────────────────────────────────────────┘
```

#### 2.2.2 Agent Role Assignment

| Phase | Primary Role | AgentRole | Task Kind |
|-------|-------------|-----------|-----------|
| Research | researcher | `researcher` | `intent`, `context` |
| Synthesis | coordinator | `planner` | `prompt` (Coordinator handles) |
| Implementation | executor | `executor` | `execution` |
| Verification | reviewer | `reviewer` | `verification`, `policy` |
| Recovery | recovery | `recovery` | `learning` |

#### 2.2.3 Token Budget Allocation Strategy

```python
class TokenBudgetAllocator:
    """Allocate token budget across multi-agent execution."""
    
    def allocate(self, total_budget: int, phases: List[PhaseInfo]) -> Dict[str, int]:
        """Distribute budget across phases and workers."""
        # Phase weights from Claude Plugin
        PHASE_WEIGHTS = {
            'research': 0.25,      # 25% for investigation
            'synthesis': 0.10,     # 10% for planning (Coordinator)
            'implementation': 0.40, # 40% for execution
            'verification': 0.25,  # 25% for validation
        }
        
        allocations = {}
        remaining = total_budget
        
        for phase in phases:
            phase_budget = int(total_budget * PHASE_WEIGHTS[phase.name])
            
            if phase.parallel_workers > 1:
                # Split among parallel workers
                worker_budget = phase_budget // phase.parallel_workers
                for i in range(phase.parallel_workers):
                    allocations[f"{phase.name}_worker_{i}"] = worker_budget
            else:
                allocations[phase.name] = phase_budget
            
            remaining -= phase_budget
        
        # Reserve for Coordinator synthesis
        allocations['synthesis'] = remaining
        
        return allocations
```

---

### 2.3 Modified Existing Modules

#### 2.3.1 orchestrator/service.py Extensions

```python
class OrchestratorService:
    """Extended with multi-agent wave scheduling."""
    
    # Existing methods preserved...
    
    def next_parallel_wave(self, task_graph: TaskGraph, max_parallel: int = 4) -> List[List[TaskNode]]:
        """Return waves of parallelizable nodes."""
        # Group ready nodes by role
        ready_nodes = self.next_wave(task_graph)
        
        # Group by role for parallel execution
        by_role = defaultdict(list)
        for node in ready_nodes:
            by_role[node.agent_role].append(node)
        
        # Create parallel batches
        waves = []
        for role, nodes in by_role.items():
            # Split into batches respecting max_parallel
            batches = [nodes[i:i+max_parallel] for i in range(0, len(nodes), max_parallel)]
            waves.extend(batches)
        
        return waves
    
    def build_multi_agent_graph(
        self,
        session: ResearchSession,
        intent: IntentDeclaration,
        workflow: WorkflowTemplateVersion | None = None,
    ) -> TaskGraph:
        """Build graph with multi-agent execution strategy."""
        graph = self.build_task_graph(session, intent, workflow)
        graph.execution_strategy = "multi_agent_wave_ready"
        
        # Assign agent roles based on task kinds
        for node in graph.nodes:
            node.agent_role = self._map_kind_to_role(node.kind)
        
        return graph
    
    def _map_kind_to_role(self, kind: str) -> AgentRole:
        """Map task kind to agent role."""
        ROLE_MAP = {
            'intent': 'planner',
            'context': 'researcher',
            'prompt': 'planner',
            'policy': 'reviewer',
            'execution': 'executor',
            'verification': 'reviewer',
            'learning': 'recovery',
        }
        return ROLE_MAP.get(kind, 'executor')
```

#### 2.3.2 runtime/service.py Extensions

```python
class RuntimeService:
    """Extended with multi-agent execution mode."""
    
    async def create_run(self, request: RunRequest) -> ResearchRun:
        """Create run with execution_mode support."""
        session = await self._ensure_session(request)
        
        run = ResearchRun(
            run_id=new_id("run"),
            session_id=session.session_id,
            status="queued",
            # ... other fields
        )
        
        # Set execution mode
        session.execution_mode = request.execution_mode
        
        if session.execution_mode == "multi_agent":
            # Use ParallelAgentCoordinator
            await self._setup_multi_agent_execution(session, run)
        else:
            # Use existing single-worker flow
            pass
        
        return run
    
    async def _setup_multi_agent_execution(self, session: ResearchRun, run: ResearchRun):
        """Initialize multi-agent coordinator."""
        coordinator = ParallelAgentCoordinator(
            orchestrator=self.orchestrator,
            dispatcher=self.dispatcher,
            config=self._load_coordinator_config(),
        )
        
        # Store coordinator reference
        run.metadata['coordinator'] = coordinator
        
        # Build multi-agent graph
        task_graph = self.orchestrator.build_multi_agent_graph(
            session,
            session.intent_declaration,
        )
        session.task_graph = task_graph
    
    async def drain_run_parallel(self, run_id: str) -> ResearchRun:
        """Execute run with parallel workers."""
        run = self.get_run(run_id)
        session = self.get_session(run.session_id)
        coordinator = run.metadata.get('coordinator')
        
        if not coordinator:
            # Fall back to single-worker
            return await self.local_worker.drain_run(run_id)
        
        # Execute four-phase workflow
        results = coordinator.run_workflow(session, run)
        
        return self.get_run(run_id)
```

#### 2.3.3 fleet/dispatcher.py Extensions

```python
class Dispatcher:
    """Extended with agent role filtering."""
    
    def next_dispatch_for_parallel_workers(
        self,
        session: ResearchSession,
        run: ResearchRun,
        parallel_wave: List[TaskNode],
    ) -> List[DispatchEnvelope]:
        """Dispatch parallel wave to multiple workers."""
        dispatches = []
        
        for node in parallel_wave:
            # Find worker matching agent role
            worker = self._find_worker_for_role(node.agent_role)
            
            if worker:
                dispatch = self._create_dispatch(run, session, node, worker.worker_id, ...)
                dispatches.append(dispatch)
        
        return dispatches
    
    def _find_worker_for_role(self, role: AgentRole) -> Optional[WorkerSnapshot]:
        """Find worker with matching role profile."""
        workers = self.worker_registry.list_workers()
        
        # Filter by role profile
        matching = [
            w for w in workers
            if w.role_profile == role and w.state == 'idle'
        ]
        
        if matching:
            # Return least busy worker
            return min(matching, key=lambda w: w.lease_count)
        
        # Fall back to any idle worker
        idle = [w for w in workers if w.state == 'idle']
        return idle[0] if idle else None
    
    def _worker_can_poll_shard(self, worker: WorkerSnapshot, shard: str) -> bool:
        """Extended with agent role matching."""
        parts = shard.split("/")
        role = parts[0] if parts else None
        
        # Check role match (new: agent_role)
        if worker.agent_role and role and worker.agent_role != role:
            return False
        
        # Existing label matching...
        return True
```

---

## Phase 3: Implementation Design

### 3.1 New Module Structure

```
backend/app/harness_lab/
├── orchestrator/
│   ├── service.py          # Existing - extend with next_parallel_wave
│   ├── coordinator.py      # NEW - ParallelAgentCoordinator
│   └── decision_engine.py  # NEW - ContinueSpawnDecisionEngine
│   └── wave_scheduler.py   # NEW - Wave scheduling logic
│
├── runtime/
│   ├── service.py          # Existing - extend with multi_agent mode
│   ├── execution_plane.py  # Existing - extend for parallel
│   ├── handoff.py          # NEW - AgentHandoffManager
│   └── worker_rules.py     # NEW - Worker Rules enforcement
│
├── fleet/
│   ├── dispatcher.py       # Existing - extend with role filtering
│   ├── agent_registry.py   # NEW - Agent type registry
│   └── worker_pool.py      # NEW - Managed worker pool
│
├── types/
│   ├── session_run.py      # Extend TaskGraph with multi_agent fields
│   ├── recovery.py         # Extend HandoffPacket
│   ├── coordinator.py      # NEW - Coordinator types
│   └── decision.py         # NEW - Decision engine types
│
└── tests/
    ├── test_coordinator.py # NEW
    ├── test_handoff.py     # NEW
    ├── test_decision.py    # NEW
    └── adapted_claude_tests/  # NEW - Adapted Claude Plugin tests
```

### 3.2 Interface Definitions

#### coordinator.py

```python
from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Any
from datetime import datetime

class CoordinatorConfig(BaseModel):
    max_parallel_workers: int = 4
    default_timeout_ms: int = 300000
    enable_verification: bool = True

class WorkflowPhase(BaseModel):
    name: str  # 'research' | 'synthesis' | 'implementation' | 'verification'
    status: str  # 'pending' | 'running' | 'completed' | 'failed'
    workers: List[str] = Field(default_factory=list)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

class WorkerResult(BaseModel):
    worker_id: str
    success: bool
    output: str
    scope: str
    key_files: List[str] = Field(default_factory=list)
    files_changed: List[Dict[str, str]] = Field(default_factory=list)
    issues: List[str] = Field(default_factory=list)
    usage: Dict[str, int] = Field(default_factory=dict)  # tokens, tool_uses, duration_ms

class TaskDirective(BaseModel):
    role: str
    directive: str
    tools: List[str] = Field(default_factory=list)
    files: List[str] = Field(default_factory=list)
    max_turns: int = 100

class CoordinatorState(BaseModel):
    session_id: str
    run_id: str
    phase: WorkflowPhase
    active_workers: Dict[str, WorkerInfo] = Field(default_factory=dict)
    completed_results: List[WorkerResult] = Field(default_factory=list)
    synthesis_output: Optional[str] = None
    implementation_plan: Optional[Dict[str, Any]] = None
```

#### decision_engine.py

```python
class TaskContext(BaseModel):
    """Context for Continue vs Spawn decision."""
    research_files: List[str] = Field(default_factory=list)
    target_files: List[str] = Field(default_factory=list)
    research_scope: str = 'broad'  # 'broad' | 'narrow'
    implementation_scope: str = 'narrow'
    is_retry: bool = False
    previous_attempt_failed: bool = False
    is_verification: bool = False
    target_worker_just_wrote_code: bool = False
    previous_approach_was_wrong: bool = False

class ContinueVsSpawnDecision(BaseModel):
    action: str  # 'continue' | 'spawn'
    reason: str
    confidence: float = 1.0
```

#### handoff.py

```python
class HandoffPacket(BaseModel):
    """Extended handoff packet."""
    id: str
    from_role: AgentRole
    to_role: AgentRole
    mission_id: Optional[str] = None
    run_id: str
    task_node_id: str
    summary: str
    artifacts: List[str] = Field(default_factory=list)
    context_refs: List[str] = Field(default_factory=list)
    required_action: str
    open_questions: List[str] = Field(default_factory=list)
    # Extensions
    worker_rules_applied: bool = False
    scope_declaration: Optional[str] = None
    created_at: str
    received_at: Optional[str] = None
```

### 3.3 Test Strategy

#### 3.3.1 Adapted Claude Plugin Tests

| Claude Test | Harness Lab Adaptation | Notes |
|-------------|------------------------|-------|
| module-01.test.ts | test_agent_definitions.py | Frontmatter → YAML, JSON parsing |
| module-02.test.ts | test_fork_subagent.py | Worker Rules, recursive protection |
| module-03.test.ts | test_task_notification.py | Task lifecycle events |
| module-04.test.ts | test_message_passing.py | Agent registry, team manager |
| module-05.test.ts | test_coordinator.py | Four-phase workflow |
| module-06.test.ts | test_permission.py | Tool filtering, bubble mode |

#### 3.3.2 New Integration Tests

```python
# test_multi_agent_integration.py

class TestMultiAgentIntegration:
    def test_parallel_research_phase(self):
        """Test parallel worker dispatch for research."""
        coordinator = ParallelAgentCoordinator(...)
        session = create_test_session()
        run = create_test_run(session)
        
        results = coordinator._run_research_phase(session, run)
        
        assert len(results) == expected_parallel_count
        assert all(r.success for r in results)
    
    def test_continue_vs_spawn_decision(self):
        """Test all 5 decision scenes."""
        engine = ContinueSpawnDecisionEngine()
        
        # Scene 1: files in context
        ctx = TaskContext(research_files=['a.py'], target_files=['a.py'])
        decision = engine.decide(ctx)
        assert decision.action == 'continue'
        
        # Scene 2: broad/narrow mismatch
        ctx = TaskContext(research_scope='broad', impl_scope='narrow')
        decision = engine.decide(ctx)
        assert decision.action == 'spawn'
        
        # ... other scenes
    
    def test_handoff_between_roles(self):
        """Test handoff from researcher to executor."""
        handoff_mgr = AgentHandoffManager(...)
        
        handoff = handoff_mgr.create_handoff(
            from_role='researcher',
            to_role='executor',
            run=test_run,
            node=test_node,
            summary="Found implementation targets",
            artifacts=['src/main.py'],
            required_action="Implement feature",
        )
        
        assert handoff.from_role == 'researcher'
        assert handoff.to_role == 'executor'
        
        result = handoff_mgr.process_handoff(handoff.id)
        assert result.ready_for_execution
    
    def test_four_phase_workflow(self):
        """Test complete workflow execution."""
        coordinator = ParallelAgentCoordinator(...)
        session = create_multi_agent_session()
        run = create_test_run(session)
        
        results = coordinator.run_workflow(session, run)
        
        assert coordinator.phase == 'completed'
        assert run.status == 'completed'
```

---

## Phase 4: Verification & Risk Assessment

### 4.1 Technical Feasibility

| Challenge | Solution |
|-----------|----------|
| TypeScript → Python | Use Pydantic for type modeling, translate interfaces |
| Pydantic vs JSON | Compatible - Pydantic models serialize to JSON |
| Async execution | Use Python asyncio, map TS Promise to await |
| Event system | Map to existing `database.append_event` |
| Message routing | Implement in handoff.py with simple routing logic |

### 4.2 Engineering Validation

| Checkpoint | Status | Notes |
|------------|--------|-------|
| TaskGraph has agent_role field | ✓ | Already defined |
| HandoffPacket exists | ✓ | Needs extension |
| Orchestrator.next_wave exists | ✓ | Extend for parallel |
| Dispatcher constraint matching | ✓ | Extend with role |
| Event system exists | ✓ | Use for task notification |
| execution_strategy field | ✓ | Change to multi_agent_wave_ready |

### 4.3 Implementation Priorities

| Priority | Task | Effort | Risk |
|----------|------|--------|------|
| P0 | ContinueSpawnDecisionEngine | 2 days | Low |
| P0 | AgentHandoffManager | 3 days | Medium |
| P0 | OrchestratorService extensions | 2 days | Low |
| P1 | ParallelAgentCoordinator | 5 days | Medium |
| P1 | Runtime multi_agent mode | 3 days | Medium |
| P1 | Dispatcher role filtering | 2 days | Low |
| P2 | Adapt Claude Plugin tests | 3 days | Low |
| P2 | Integration tests | 2 days | Low |
| P3 | Token budget allocator | 2 days | Low |
| P3 | Worker pool management | 3 days | Medium |

### 4.4 Backward Compatibility

```python
# execution_mode selection
if request.execution_mode == "multi_agent":
    # Use new multi-agent flow
    coordinator = ParallelAgentCoordinator(...)
else:
    # Preserve existing single-worker flow
    await self.local_worker.drain_run(run_id)
```

**Guarantees:**
- Default execution_mode = "single_worker" (unchanged behavior)
- Multi-agent mode opt-in via request parameter
- Existing API endpoints unchanged
- TaskGraph structure preserved

### 4.5 Rollback Strategy

1. **Phase Detection:** Check `execution_mode` at run creation
2. **Fallback Path:** If multi_agent fails, convert to single_worker
3. **State Preservation:** All state stored in database, recoverable
4. **Feature Flag:** Environment variable `HARNESS_MULTI_AGENT_ENABLED`

```python
MULTI_AGENT_ENABLED = os.getenv('HARNESS_MULTI_AGENT_ENABLED', 'false') == 'true'

async def create_run(self, request: RunRequest):
    if request.execution_mode == "multi_agent" and not MULTI_AGENT_ENABLED:
        request.execution_mode = "single_worker"  # Auto fallback
```

---

## Appendix A: Worker Rules Integration

```python
# runtime/worker_rules.py

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

def build_worker_directive(
    role: AgentRole,
    task: str,
    files: List[str] = None,
    tools: List[str] = None,
    max_turns: int = 100,
) -> str:
    """Build worker directive with embedded rules."""
    return f"{WORKER_RULES_TEMPLATE}\nYour directive: {task}"
```

---

## Appendix B: Agent Role Mapping

| Claude Plugin Type | Harness Lab AgentRole | Tools (Simple) | Tools (Full) |
|--------------------|----------------------|----------------|--------------|
| research | researcher | Bash, Read, Grep | All async tools |
| implementation | executor | Bash, Read, Edit | All async tools |
| verification | reviewer | Bash, Read, Grep | All async tools |
| custom | executor | Configurable | Configurable |

---

## Appendix C: Token Budget Formula

```
Phase Budget = Total Budget × Phase Weight

Research:    Total × 0.25
Synthesis:   Total × 0.10
Implementation: Total × 0.40
Verification: Total × 0.25

Parallel Worker Budget = Phase Budget ÷ Worker Count
```

---

## Summary

This design transforms Harness Lab from a single-worker execution model into a parallel multi-agent orchestration system by:

1. **Preserving existing architecture** - All current components remain intact
2. **Adding new modules** - Coordinator, Handoff, Decision Engine
3. **Extending existing modules** - Orchestrator, Runtime, Dispatcher
4. **Opt-in activation** - execution_mode="multi_agent"
5. **Backward compatible** - Default single_worker unchanged

**Estimated Effort:** 15-20 days for P0/P1 implementation
**Risk Level:** Medium (new components, but existing architecture preserved)
**Test Coverage:** 46 Claude Plugin tests adapted + new integration tests