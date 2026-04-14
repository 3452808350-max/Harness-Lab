from __future__ import annotations

from typing import Dict, List, TYPE_CHECKING

from ..types import IntentDeclaration, ResearchSession, TaskEdge, TaskGraph, TaskNode, WorkflowTemplateVersion
from ..types.base import AgentRole
from ..utils import new_id

if TYPE_CHECKING:
    pass


class OrchestratorService:
    """DAG-native orchestrator with multi-agent wave support.
    
    Supports both single-worker and multi-agent execution strategies:
    - single_worker_wave_ready: Sequential execution by one worker
    - multi_agent_wave_ready: Parallel execution with role-based dispatch
    
    Design source: Claude Plugin Module 05 - Coordinator Mode
    See: design/claude-plugin-integration-design.md
    """

    def build_task_graph(
        self,
        session: ResearchSession,
        intent: IntentDeclaration,
        workflow: WorkflowTemplateVersion | None = None,
    ) -> TaskGraph:
        if workflow and workflow.dag.get("nodes"):
            nodes_by_key = {}
            nodes = []
            for index, node_spec in enumerate(workflow.dag.get("nodes", [])):
                key = str(node_spec.get("key") or f"node_{index}")
                label = str(node_spec.get("label") or key)
                if key == "execute":
                    label = f"Execute {intent.suggested_action.tool_name}"
                node = TaskNode(
                    node_id=new_id("node"),
                    label=label,
                    kind=str(node_spec.get("kind") or "task"),
                    role=str(node_spec.get("role") or "executor"),
                    agent_role=str(node_spec.get("role") or "executor"),
                    metadata={
                        "key": key,
                        "sequence": index,
                        "session_id": session.session_id,
                        "workflow_template_id": workflow.workflow_id,
                        "suggested_tool": intent.suggested_action.tool_name if key == "execute" else None,
                        "gates": workflow.gates,
                    },
                )
                nodes.append(node)
                nodes_by_key[key] = node
            edges = []
            for edge_spec in workflow.dag.get("edges", []):
                source = nodes_by_key.get(str(edge_spec.get("source")))
                target = nodes_by_key.get(str(edge_spec.get("target")))
                if not source or not target:
                    continue
                edge_kind = str(edge_spec.get("kind") or "depends_on")
                if edge_kind in {"depends_on", "handoff"}:
                    target.dependencies.append(source.node_id)
                edges.append(
                    TaskEdge(
                        edge_id=new_id("edge"),
                        source=source.node_id,
                        target=target.node_id,
                        kind=edge_kind,
                    )
                )
            return TaskGraph(task_graph_id=new_id("graph"), nodes=nodes, edges=edges, execution_strategy="multi_agent_wave_ready")

        node_specs = [
            ("intent", "Declare intent", "intent", "planner"),
            ("context", "Assemble layered context", "context", "planner"),
            ("prompt", "Render structured prompt frame", "prompt", "planner"),
            ("policy", "Run constraint preflight", "policy", "reviewer"),
            ("execute", f"Execute {intent.suggested_action.tool_name}", "execution", "executor"),
            ("verify", "Verify trace and outcome", "verification", "reviewer"),
            ("learn", "Write research learning artifacts", "learning", "recovery"),
        ]
        nodes = [
            TaskNode(
                node_id=new_id("node"),
                label=label,
                kind=kind,
                role=role,
                agent_role=role,
                metadata={
                    "sequence": index,
                    "session_id": session.session_id,
                    "suggested_tool": intent.suggested_action.tool_name if key == "execute" else None,
                },
            )
            for index, (key, label, kind, role) in enumerate(node_specs)
        ]
        edges = []
        for left, right in zip(nodes, nodes[1:]):
            right.dependencies.append(left.node_id)
            edges.append(TaskEdge(edge_id=new_id("edge"), source=left.node_id, target=right.node_id))
        return TaskGraph(task_graph_id=new_id("graph"), nodes=nodes, edges=edges, execution_strategy="single_worker_wave_ready")

    def next_wave(self, task_graph: TaskGraph) -> List[TaskNode]:
        nodes_by_id = {node.node_id: node for node in task_graph.nodes}
        ready: List[TaskNode] = []
        for node in task_graph.nodes:
            if node.status not in {"planned", "ready"}:
                continue
            inbound_edges = [edge for edge in task_graph.edges if edge.target == node.node_id]
            if not inbound_edges:
                ready.append(node)
                continue
            if all(self._edge_satisfied(edge, nodes_by_id) for edge in inbound_edges):
                ready.append(node)
        return ready

    def mark_node_status(
        self,
        task_graph: TaskGraph,
        node_id: str,
        status: str,
        metadata: Dict[str, object] | None = None,
    ) -> TaskNode:
        node = self._node_by_id(task_graph, node_id)
        node.status = status
        if metadata:
            node.metadata.update(metadata)
        return node

    def skip_unreachable_nodes(self, task_graph: TaskGraph) -> List[TaskNode]:
        nodes_by_id = {node.node_id: node for node in task_graph.nodes}
        skipped: List[TaskNode] = []
        for node in task_graph.nodes:
            if node.status not in {"planned", "ready"}:
                continue
            inbound_edges = [edge for edge in task_graph.edges if edge.target == node.node_id]
            if not inbound_edges:
                continue
            if all(self._edge_unreachable(edge, nodes_by_id) for edge in inbound_edges):
                node.status = "skipped"
                node.metadata["skip_reason"] = "upstream_condition_not_met"
                skipped.append(node)
        return skipped

    def is_terminal(self, task_graph: TaskGraph) -> bool:
        return all(node.status in {"completed", "failed", "skipped", "blocked"} for node in task_graph.nodes)

    def has_failed_nodes(self, task_graph: TaskGraph) -> bool:
        return any(node.status == "failed" for node in task_graph.nodes)

    def has_node_kind(self, task_graph: TaskGraph, kind: str) -> bool:
        return any(node.kind == kind for node in task_graph.nodes)

    @staticmethod
    def _edge_satisfied(edge: TaskEdge, nodes_by_id: Dict[str, TaskNode]) -> bool:
        source = nodes_by_id[edge.source]
        if edge.kind == "on_failure":
            return source.status == "failed"
        if edge.kind == "handoff":
            return source.status in {"completed", "failed", "skipped"}
        return source.status == "completed"

    @staticmethod
    def _edge_unreachable(edge: TaskEdge, nodes_by_id: Dict[str, TaskNode]) -> bool:
        source = nodes_by_id[edge.source]
        if edge.kind == "on_failure":
            return source.status in {"completed", "skipped"}
        if edge.kind == "handoff":
            return False
        return source.status in {"failed", "skipped"}

    @staticmethod
    def _node_by_id(task_graph: TaskGraph, node_id: str) -> TaskNode:
        for node in task_graph.nodes:
            if node.node_id == node_id:
                return node
        raise ValueError(f"Task node not found: {node_id}")
    
    # === Multi-Agent Extensions ===
    # Design source: Claude Plugin Module 05 - Coordinator Mode
    
    def next_parallel_wave(self, task_graph: TaskGraph, max_parallel: int = 4) -> List[List[TaskNode]]:
        """Return ready nodes grouped for parallel execution.
        
        Groups ready nodes by role to enable role-based parallel dispatch.
        Each inner list is a wave of nodes that can execute simultaneously.
        
        Grouping rules:
        - Same role → can parallelize (up to max_parallel)
        - Different roles → parallelize within role
        - Handoff edges → preserve ordering
        
        Args:
            task_graph: TaskGraph to analyze
            max_parallel: Maximum nodes per wave
            
        Returns:
            List of waves (each wave is List[TaskNode])
        """
        # Get all ready nodes
        ready_nodes = self.next_wave(task_graph)
        
        if not ready_nodes:
            return []
        
        # Group by role
        role_groups: Dict[AgentRole, List[TaskNode]] = {}
        for node in ready_nodes:
            role = node.agent_role
            if role not in role_groups:
                role_groups[role] = []
            role_groups[role].append(node)
        
        # Build parallel waves
        waves: List[List[TaskNode]] = []
        
        # First, handle handoff dependencies (must be sequential)
        handoff_nodes = [
            node for node in ready_nodes
            if any(
                edge.kind == "handoff" and edge.target == node.node_id
                for edge in task_graph.edges
            )
        ]
        
        # Handoff nodes go first, one per wave
        for node in handoff_nodes:
            waves.append([node])
        
        # Then, parallelize remaining nodes by role
        remaining = [n for n in ready_nodes if n not in handoff_nodes]
        
        # Group remaining by role and create parallel waves
        for role, nodes in role_groups.items():
            # Skip handoff nodes already processed
            role_remaining = [n for n in nodes if n not in handoff_nodes]
            
            # Split into batches respecting max_parallel
            for i in range(0, len(role_remaining), max_parallel):
                batch = role_remaining[i:i + max_parallel]
                if batch:
                    # Try to merge with existing wave if possible
                    merged = False
                    for wave in waves:
                        # Can merge if all nodes in wave have same role
                        wave_roles = {n.agent_role for n in wave}
                        if len(wave_roles) == 1 and wave_roles.pop() == role:
                            if len(wave) + len(batch) <= max_parallel:
                                wave.extend(batch)
                                merged = True
                                break
                    if not merged:
                        waves.append(batch)
        
        return waves
    
    def build_multi_agent_graph(
        self,
        session: ResearchSession,
        intent: IntentDeclaration,
        workflow: WorkflowTemplateVersion | None = None,
    ) -> TaskGraph:
        """Build task graph optimized for multi-agent execution.
        
        Similar to build_task_graph but with multi-agent optimizations:
        - Role-based node assignment
        - Handoff edge annotations
        - Parallel execution markers
        
        Args:
            session: ResearchSession context
            intent: IntentDeclaration with suggested action
            workflow: Optional workflow template
            
        Returns:
            TaskGraph with multi_agent_wave_ready strategy
        """
        # Use workflow if provided
        if workflow and workflow.dag.get("nodes"):
            return self.build_task_graph(session, intent, workflow)
        
        # Build multi-agent optimized graph
        node_specs = [
            ("intent", "Declare intent", "intent", "planner"),
            ("context", "Assemble layered context", "context", "planner"),
            ("prompt", "Render structured prompt frame", "prompt", "planner"),
            ("policy", "Run constraint preflight", "policy", "reviewer"),
            # Handoff point: planner/reviewer → executor
            ("execute", f"Execute {intent.suggested_action.tool_name}", "execution", "executor"),
            # Handoff point: executor → reviewer (verification)
            ("verify", "Verify trace and outcome", "verification", "reviewer"),
            # Handoff point: reviewer → recovery (learning)
            ("learn", "Write research learning artifacts", "learning", "recovery"),
        ]
        
        nodes = [
            TaskNode(
                node_id=new_id("node"),
                label=label,
                kind=kind,
                role=role,
                agent_role=role,
                metadata={
                    "sequence": index,
                    "session_id": session.session_id,
                    "suggested_tool": intent.suggested_action.tool_name if key == "execute" else None,
                    "multi_agent_eligible": True,
                },
            )
            for index, (key, label, kind, role) in enumerate(node_specs)
        ]
        
        # Build edges with handoff annotations at role transitions
        edges = []
        for i, (left, right) in enumerate(zip(nodes, nodes[1:])):
            right.dependencies.append(left.node_id)
            
            # Mark handoff edges at role transitions
            edge_kind = "depends_on"
            if left.agent_role != right.agent_role:
                edge_kind = "handoff"
            
            edges.append(
                TaskEdge(
                    edge_id=new_id("edge"),
                    source=left.node_id,
                    target=right.node_id,
                    kind=edge_kind,
                )
            )
        
        return TaskGraph(
            task_graph_id=new_id("graph"),
            nodes=nodes,
            edges=edges,
            execution_strategy="multi_agent_wave_ready",
        )
    
    def _map_kind_to_role(self, kind: str) -> AgentRole:
        """Map task kind to agent role.
        
        Used by dispatcher for role-based worker matching.
        
        Args:
            kind: Task kind (e.g., 'intent', 'execution', 'verification')
            
        Returns:
            AgentRole for the task kind
        """
        KIND_TO_ROLE: Dict[str, AgentRole] = {
            "intent": "planner",
            "context": "planner",
            "prompt": "planner",
            "policy": "reviewer",
            "execution": "executor",
            "verify": "reviewer",
            "verification": "reviewer",
            "learn": "recovery",
            "learning": "recovery",
            "recovery": "recovery",
            "review": "reviewer",
            "task": "executor",
            "research": "researcher",
        }
        return KIND_TO_ROLE.get(kind, "executor")
    
    def get_nodes_by_role(self, task_graph: TaskGraph, role: AgentRole) -> List[TaskNode]:
        """Get all nodes with specified role.
        
        Args:
            task_graph: TaskGraph
            role: AgentRole to filter
            
        Returns:
            List of TaskNode with matching role
        """
        return [node for node in task_graph.nodes if node.agent_role == role]
    
    def get_ready_nodes_by_role(self, task_graph: TaskGraph, role: AgentRole) -> List[TaskNode]:
        """Get ready nodes with specified role.
        
        Args:
            task_graph: TaskGraph
            role: AgentRole to filter
            
        Returns:
            List of ready TaskNode with matching role
        """
        ready_nodes = self.next_wave(task_graph)
        return [node for node in ready_nodes if node.agent_role == role]
    
    def count_parallel_eligible(self, task_graph: TaskGraph) -> int:
        """Count nodes eligible for parallel execution.
        
        Nodes are parallel eligible if:
        - Status is 'planned' or 'ready'
        - No handoff edge dependency
        - Has multi_agent_eligible metadata
        
        Args:
            task_graph: TaskGraph
            
        Returns:
            Count of parallel eligible nodes
        """
        eligible = 0
        for node in task_graph.nodes:
            if node.status not in {"planned", "ready"}:
                continue
            
            # Check for handoff edge
            has_handoff_dep = any(
                edge.kind == "handoff" and edge.target == node.node_id
                for edge in task_graph.edges
            )
            
            if has_handoff_dep:
                continue
            
            if node.metadata.get("multi_agent_eligible"):
                eligible += 1
        
        return eligible
