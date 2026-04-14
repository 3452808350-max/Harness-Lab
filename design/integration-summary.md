# Claude Plugin Integration Summary

## Quick Reference

### Architecture Mapping

| Claude Plugin | Harness Lab | Integration Point |
|--------------|-------------|-------------------|
| Coordinator Mode | OrchestratorService | `orchestrator/coordinator.py` |
| Fork Subagent | Dispatcher + Fleet | Role filtering in `fleet/dispatcher.py` |
| Task Notification | Event System | `database.append_event` |
| Message Passing | HandoffPacket | `runtime/handoff.py` |
| Continue vs Spawn | Decision Engine | `orchestrator/decision_engine.py` |

### Key Gaps Identified

1. **Orchestrator**: `next_wave()` returns all ready nodes, but no parallel grouping
2. **Dispatcher**: No agent role filtering, only label-based
3. **Runtime**: Single-worker `drain_run()` loop
4. **TaskGraph**: `execution_strategy="single_worker_wave_ready"` unused

### P0 Implementation (Critical Path)

```
orchestrator/decision_engine.py  - Continue vs Spawn logic (2 days)
runtime/handoff.py               - Agent handoff manager (3 days)
orchestrator/service.py          - next_parallel_wave() (2 days)
```

### P1 Implementation (Core Features)

```
orchestrator/coordinator.py      - ParallelAgentCoordinator (5 days)
runtime/service.py               - multi_agent execution_mode (3 days)
fleet/dispatcher.py              - Role filtering (2 days)
```

### Test Strategy

- Adapt 46 Claude Plugin tests to Python
- New integration tests for workflow phases
- Backward compatibility tests for single_worker mode

### Risk Mitigation

- Feature flag: `HARNESS_MULTI_AGENT_ENABLED`
- Auto fallback: multi_agent → single_worker if disabled
- All state in database (recoverable)

---

## Design Document Location

Full design: `/home/kyj/.openclaw/workspace/code-flow/design/claude-plugin-integration-design.md`