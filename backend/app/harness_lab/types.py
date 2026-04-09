from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


SessionStatus = Literal[
    "configured",
    "running",
    "awaiting_approval",
    "awaiting_escalation",
    "completed",
    "failed",
]
RunStatus = Literal[
    "queued",
    "planning",
    "running",
    "awaiting_approval",
    "awaiting_escalation",
    "recovering",
    "completed",
    "failed",
    "cancelled",
]
ConstraintStatus = Literal["candidate", "published", "archived"]
ProfileStatus = Literal["candidate", "published", "archived"]
VerdictDecision = Literal["allow", "approval_required", "deny"]
ApprovalDecision = Literal["approve", "deny", "approve_once"]
ApprovalStatus = Literal["pending", "approved", "denied", "consumed"]
ContextLayer = Literal["structure", "task", "history", "index"]
CandidateKind = Literal["policy", "workflow"]
CandidatePublishStatus = Literal["draft", "evaluating", "awaiting_approval", "publish_ready", "published", "rolled_back", "rejected"]
EvaluationStatus = Literal["pending", "passed", "failed"]
EvaluationSuite = Literal["replay", "benchmark"]
WorkerState = Literal["registering", "idle", "leased", "executing", "draining", "offline", "unhealthy"]
MissionStatus = Literal["queued", "running", "awaiting_approval", "completed", "failed", "cancelled"]
AttemptStatus = Literal["leased", "running", "completed", "failed", "blocked", "released", "expired"]
LeaseStatus = Literal["leased", "running", "completed", "failed", "released", "expired"]


class ActionPlan(BaseModel):
    tool_name: str
    subject: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    summary: str = ""


class IntentDeclaration(BaseModel):
    intent_id: str
    task_type: str
    intent: str
    confidence: float
    risk_mode: str
    suggested_action: ActionPlan
    model_profile_id: str
    created_at: str


class ModelProviderSettings(BaseModel):
    provider: str
    api_key_present: bool
    base_url: str
    model_name: str
    model_ready: bool
    fallback_mode: bool


class ModelCallTrace(BaseModel):
    provider: str
    model_name: str
    latency_ms: int
    used_fallback: bool = False
    failure_reason: Optional[str] = None


class ContextBlock(BaseModel):
    context_block_id: str
    layer: ContextLayer
    type: str
    title: str
    source_ref: str
    content: str
    score: float
    token_estimate: int
    selected: bool
    dependencies: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PromptSection(BaseModel):
    section_key: str
    title: str
    content: str
    token_estimate: int
    source_refs: List[str] = Field(default_factory=list)


class PromptFrame(BaseModel):
    prompt_frame_id: str
    template_id: str
    sections: List[PromptSection] = Field(default_factory=list)
    total_token_estimate: int
    truncated_blocks: List[str] = Field(default_factory=list)
    created_at: str


class TaskNode(BaseModel):
    node_id: str
    label: str
    kind: str
    role: str = "executor"
    status: str = "planned"
    dependencies: List[str] = Field(default_factory=list)
    context_packet_ref: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TaskEdge(BaseModel):
    edge_id: str
    source: str
    target: str
    kind: str = "depends_on"


class TaskGraph(BaseModel):
    task_graph_id: str
    nodes: List[TaskNode] = Field(default_factory=list)
    edges: List[TaskEdge] = Field(default_factory=list)
    execution_strategy: str = "single_worker_wave_ready"


class PolicyVerdict(BaseModel):
    verdict_id: str
    subject: str
    decision: VerdictDecision
    reason: str
    matched_rule: str
    created_at: str


class ToolCallRecord(BaseModel):
    tool_name: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    ok: bool
    output: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    created_at: str


class RecoveryEvent(BaseModel):
    recovery_id: str
    kind: str
    summary: str
    created_at: str


class ArtifactRef(BaseModel):
    artifact_id: str
    run_id: Optional[str] = None
    artifact_type: str
    relative_path: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str


class ApprovalRequestModel(BaseModel):
    approval_id: str
    run_id: str
    verdict_id: str
    subject: str
    summary: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    status: ApprovalStatus
    decision: Optional[ApprovalDecision] = None
    created_at: str
    updated_at: str


class ExecutionTrace(BaseModel):
    trace_id: str
    session_id: str
    prompt_frame_id: str
    intent_declaration: IntentDeclaration
    model_calls: List[ModelCallTrace] = Field(default_factory=list)
    context_blocks: List[ContextBlock] = Field(default_factory=list)
    policy_verdicts: List[PolicyVerdict] = Field(default_factory=list)
    tool_calls: List[ToolCallRecord] = Field(default_factory=list)
    recovery_events: List[RecoveryEvent] = Field(default_factory=list)
    artifacts: List[ArtifactRef] = Field(default_factory=list)
    status: str
    created_at: str
    updated_at: str


class ConstraintDocument(BaseModel):
    document_id: str
    title: str
    body: str
    scope: str
    status: ConstraintStatus
    tags: List[str] = Field(default_factory=list)
    priority: int = 50
    source: str = "manual"
    version: str = "v1"
    created_at: str
    updated_at: str


class ContextProfile(BaseModel):
    context_profile_id: str
    name: str
    description: str
    status: ProfileStatus
    config: Dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class PromptTemplate(BaseModel):
    prompt_template_id: str
    name: str
    description: str
    status: ProfileStatus
    sections: List[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


class ModelProfile(BaseModel):
    model_profile_id: str
    name: str
    provider: str
    profile: str
    status: ProfileStatus
    config: Dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class HarnessPolicy(BaseModel):
    policy_id: str
    name: str
    status: ProfileStatus
    constraint_set_id: str
    context_profile_id: str
    prompt_template_id: str
    model_profile_id: str
    repair_policy: Dict[str, Any] = Field(default_factory=dict)
    budget_policy: Dict[str, Any] = Field(default_factory=dict)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class WorkflowTemplateVersion(BaseModel):
    workflow_id: str
    parent_id: Optional[str] = None
    name: str
    description: str
    scope: str = "global"
    status: ProfileStatus
    dag: Dict[str, Any] = Field(default_factory=dict)
    role_map: Dict[str, Any] = Field(default_factory=dict)
    gates: List[Dict[str, Any]] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class ResearchSession(BaseModel):
    session_id: str
    goal: str
    status: SessionStatus
    active_policy_id: str
    workflow_template_id: Optional[str] = None
    constraint_set_id: str
    context_profile_id: str
    prompt_template_id: str
    model_profile_id: str
    execution_mode: str
    context: Dict[str, Any] = Field(default_factory=dict)
    intent_declaration: Optional[IntentDeclaration] = None
    intent_model_call: Optional[ModelCallTrace] = None
    task_graph: Optional[TaskGraph] = None
    created_at: str
    updated_at: str


class ResearchRun(BaseModel):
    run_id: str
    session_id: str
    status: RunStatus
    mission_id: Optional[str] = None
    policy_id: Optional[str] = None
    workflow_template_id: Optional[str] = None
    assigned_worker_id: Optional[str] = None
    current_attempt_id: Optional[str] = None
    active_lease_id: Optional[str] = None
    prompt_frame: Optional[PromptFrame] = None
    execution_trace: Optional[ExecutionTrace] = None
    result: Dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class ExperimentRun(BaseModel):
    experiment_id: str
    scenario_suite: str
    harness_ids: List[str] = Field(default_factory=list)
    status: str
    metrics: Dict[str, Any] = Field(default_factory=dict)
    trace_refs: List[str] = Field(default_factory=list)
    winner: Optional[str] = None
    created_at: str
    updated_at: str


class ImprovementCandidate(BaseModel):
    candidate_id: str
    kind: CandidateKind
    target_id: str
    target_version_id: str
    baseline_version_id: Optional[str] = None
    change_set: Dict[str, Any] = Field(default_factory=dict)
    rationale: str
    eval_status: str = "pending"
    publish_status: CandidatePublishStatus = "draft"
    approved: bool = False
    requires_human_approval: bool = False
    metrics: Dict[str, Any] = Field(default_factory=dict)
    evaluation_ids: List[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


class EvaluationFailure(BaseModel):
    kind: str
    severity: Literal["hard", "soft"]
    bucket: Optional[str] = None
    trace_ref: Optional[str] = None
    summary: str


class BenchmarkBucketResult(BaseModel):
    bucket: str
    total: int
    passed: int
    failed: int
    coverage: float
    regressions: List[str] = Field(default_factory=list)


class EvaluationSuiteManifest(BaseModel):
    suite_id: str
    source: str
    trace_refs: List[str] = Field(default_factory=list)
    bucket_map: Dict[str, List[str]] = Field(default_factory=dict)
    eligibility: Dict[str, Any] = Field(default_factory=dict)
    generated_at: str


class EvaluationReport(BaseModel):
    evaluation_id: str
    candidate_id: Optional[str] = None
    suite: EvaluationSuite
    status: EvaluationStatus
    success_rate: float
    safety_score: float
    recovery_score: float
    regression_count: int
    suite_manifest: Optional[EvaluationSuiteManifest] = None
    bucket_results: List[BenchmarkBucketResult] = Field(default_factory=list)
    hard_failures: List[EvaluationFailure] = Field(default_factory=list)
    soft_regressions: List[EvaluationFailure] = Field(default_factory=list)
    coverage_gaps: List[str] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    trace_refs: List[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


class PublishGateStatus(BaseModel):
    candidate_id: str
    replay_passed: bool
    benchmark_passed: bool
    approval_required: bool
    approval_satisfied: bool
    publish_ready: bool
    blockers: List[str] = Field(default_factory=list)
    latest_replay_evaluation_id: Optional[str] = None
    latest_benchmark_evaluation_id: Optional[str] = None


class FailureCluster(BaseModel):
    cluster_id: str
    signature: str
    frequency: int
    affected_policies: List[str] = Field(default_factory=list)
    affected_workflows: List[str] = Field(default_factory=list)
    sample_run_ids: List[str] = Field(default_factory=list)
    summary: str
    created_at: str
    updated_at: str


class WorkerSnapshot(BaseModel):
    worker_id: str
    label: str
    state: WorkerState
    capabilities: List[str] = Field(default_factory=list)
    hostname: Optional[str] = None
    pid: Optional[int] = None
    labels: List[str] = Field(default_factory=list)
    execution_mode: str = "embedded"
    heartbeat_at: str
    lease_count: int = 0
    version: str = "v1"
    current_run_id: Optional[str] = None
    current_task_node_id: Optional[str] = None
    current_lease_id: Optional[str] = None
    last_error: Optional[str] = None
    created_at: str
    updated_at: str


class Mission(BaseModel):
    mission_id: str
    session_id: str
    run_id: str
    status: MissionStatus
    created_at: str
    updated_at: str


class TaskAttempt(BaseModel):
    attempt_id: str
    run_id: str
    task_node_id: str
    worker_id: Optional[str] = None
    lease_id: Optional[str] = None
    status: AttemptStatus
    retry_index: int = 0
    summary: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    created_at: str
    updated_at: str


class WorkerLease(BaseModel):
    lease_id: str
    worker_id: str
    run_id: str
    task_node_id: str
    attempt_id: str
    status: LeaseStatus
    approval_token: Optional[str] = None
    expires_at: str
    heartbeat_at: str
    created_at: str
    updated_at: str


class DispatchEnvelope(BaseModel):
    lease_id: str
    attempt_id: str
    run_id: str
    task_node_id: str
    task_node: TaskNode
    intent: IntentDeclaration
    mission_id: Optional[str] = None
    context_packet_ref: Optional[str] = None
    prompt_frame_ref: Optional[str] = None
    policy_verdicts: List[PolicyVerdict] = Field(default_factory=list)
    budget: Dict[str, Any] = Field(default_factory=dict)
    tool_policy: Dict[str, Any] = Field(default_factory=dict)
    approval_token: Optional[str] = None
    lease_timeout_seconds: int = 30
    heartbeat_interval_seconds: int = 10
    run_status_hint: Optional[str] = None
    created_at: str


class WorkerEventRecord(BaseModel):
    event_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)


class WorkerEventBatch(BaseModel):
    lease_id: str
    events: List[WorkerEventRecord] = Field(default_factory=list)
    model_calls: List[ModelCallTrace] = Field(default_factory=list)
    tool_calls: List[ToolCallRecord] = Field(default_factory=list)
    artifacts: List[ArtifactRef] = Field(default_factory=list)
    recovery_events: List[RecoveryEvent] = Field(default_factory=list)
    emitted_at: str


class LeaseTransitionContext(BaseModel):
    lease: WorkerLease
    attempt: TaskAttempt
    run: ResearchRun
    session: ResearchSession
    task_node: TaskNode
    timestamp: str


class RunCoordinationSnapshot(BaseModel):
    run_id: str
    mission_status: Optional[str] = None
    counts_by_status: Dict[str, int] = Field(default_factory=dict)
    node_ids_by_status: Dict[str, List[str]] = Field(default_factory=dict)
    active_lease_id: Optional[str] = None
    current_attempt_id: Optional[str] = None
    updated_at: str


class WorkerHealthSummary(BaseModel):
    worker_id: str
    derived_state: WorkerState
    active_lease_count: int = 0
    recent_lease_ids: List[str] = Field(default_factory=list)
    recent_error_events: List[Dict[str, Any]] = Field(default_factory=list)
    last_event_types: List[str] = Field(default_factory=list)
    last_heartbeat_at: str
    current_run_id: Optional[str] = None
    current_task_node_id: Optional[str] = None


class StuckRunSummary(BaseModel):
    run_id: str
    session_id: str
    status: RunStatus
    mission_status: Optional[MissionStatus] = None
    reason: str
    age_seconds: int
    updated_at: str


class DoctorReport(BaseModel):
    control_plane: Dict[str, Any] = Field(default_factory=dict)
    provider: Dict[str, Any] = Field(default_factory=dict)
    workers: Dict[str, Any] = Field(default_factory=dict)
    improvement_plane: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)


class EventEnvelope(BaseModel):
    seq: int
    event_id: str
    session_id: Optional[str] = None
    run_id: Optional[str] = None
    event_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: str


class ToolDescriptor(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    description: str
    risk_level: str
    timeout_ms: int
    side_effect_class: str
    input_schema: Dict[str, Any] = Field(default_factory=dict, alias="schema", serialization_alias="schema")


class ToolExecutionResult(BaseModel):
    ok: bool
    output: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None


class SessionRequest(BaseModel):
    goal: str
    context: Dict[str, Any] = Field(default_factory=dict)
    constraint_set_id: Optional[str] = None
    context_profile_id: Optional[str] = None
    prompt_template_id: Optional[str] = None
    model_profile_id: Optional[str] = None
    workflow_template_id: Optional[str] = None
    execution_mode: str = "single_worker"


class ConstraintCreateRequest(BaseModel):
    title: str
    body: str
    scope: str = "global"
    tags: List[str] = Field(default_factory=list)
    priority: int = 50
    source: str = "manual"


class IntentRequest(BaseModel):
    goal: str
    session_id: Optional[str] = None
    context: Dict[str, Any] = Field(default_factory=dict)
    model_profile_id: Optional[str] = None


class ContextAssembleRequest(BaseModel):
    goal: Optional[str] = None
    session_id: Optional[str] = None
    context: Dict[str, Any] = Field(default_factory=dict)
    context_profile_id: Optional[str] = None


class PromptRenderRequest(BaseModel):
    session_id: str


class ConstraintVerifyRequest(BaseModel):
    subject: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    constraint_set_id: Optional[str] = None


class RunRequest(BaseModel):
    session_id: Optional[str] = None
    goal: Optional[str] = None
    context: Dict[str, Any] = Field(default_factory=dict)
    constraint_set_id: Optional[str] = None
    context_profile_id: Optional[str] = None
    prompt_template_id: Optional[str] = None
    model_profile_id: Optional[str] = None
    workflow_template_id: Optional[str] = None
    execution_mode: str = "single_worker"


class ApprovalDecisionRequest(BaseModel):
    decision: ApprovalDecision


class PolicyCompareRequest(BaseModel):
    policy_ids: List[str] = Field(default_factory=list)


class WorkflowCompareRequest(BaseModel):
    workflow_ids: List[str] = Field(default_factory=list)


class ExperimentRequest(BaseModel):
    scenario_suite: str = "golden_trace"
    harness_ids: List[str] = Field(default_factory=list)
    trace_refs: List[str] = Field(default_factory=list)


class PolicyCandidateRequest(BaseModel):
    policy_id: Optional[str] = None
    trace_refs: List[str] = Field(default_factory=list)
    rationale: Optional[str] = None


class WorkflowCandidateRequest(BaseModel):
    workflow_id: Optional[str] = None
    trace_refs: List[str] = Field(default_factory=list)
    rationale: Optional[str] = None


class EvaluationRequest(BaseModel):
    candidate_id: Optional[str] = None
    trace_refs: List[str] = Field(default_factory=list)
    suite_config: Dict[str, Any] = Field(default_factory=dict)


class WorkerRegisterRequest(BaseModel):
    worker_id: Optional[str] = None
    label: str = "local-worker"
    capabilities: List[str] = Field(default_factory=list)
    hostname: Optional[str] = None
    pid: Optional[int] = None
    labels: List[str] = Field(default_factory=list)
    execution_mode: str = "embedded"
    version: str = "v1"


class WorkerHeartbeatRequest(BaseModel):
    state: WorkerState = "idle"
    lease_count: int = 0
    current_run_id: Optional[str] = None
    current_task_node_id: Optional[str] = None
    current_lease_id: Optional[str] = None
    last_error: Optional[str] = None


class WorkerPollRequest(BaseModel):
    max_tasks: int = 1


class WorkerPollResponse(BaseModel):
    dispatches: List[DispatchEnvelope] = Field(default_factory=list)


class LeaseSweepReport(BaseModel):
    scanned: int = 0
    reclaimed: int = 0
    expired_lease_ids: List[str] = Field(default_factory=list)


class LeaseCompletionRequest(BaseModel):
    worker_event_batch: Optional[WorkerEventBatch] = None
    summary: Optional[str] = None


class LeaseFailureRequest(BaseModel):
    worker_event_batch: Optional[WorkerEventBatch] = None
    error: str


class LeaseReleaseRequest(BaseModel):
    reason: Optional[str] = None
