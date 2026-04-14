# Harness Lab

Harness Lab 是一个研究优先、可回放、面向生产化演进的多 agent 平台。它不是旧工作流产品，也还不是最终形态的 agent 云，但当前已经具备一条完整的控制平面、执行边界、约束治理和前端工作台链路。

## What It Is

- `backend/app/harness_lab/context`: 分层 context 管理
- `backend/app/harness_lab/constraints`: 自然语言约束解析、编译与 policy verdict
- `backend/app/harness_lab/boundary`: sandbox 执行边界、patch staging、workspace 审计
- `backend/app/harness_lab/fleet`: worker、lease、dispatch、协议和适配层
- `backend/app/harness_lab/runtime`: session / run / mission / attempt / lease runtime
- `backend/app/harness_lab/improvement`: candidate、eval harness、publish gate、canary
- `backend/app/harness_lab/control_plane`: sessions、runs、workers、leases、replays、evals API
- `backend/app/harness_lab/orchestrator`: task graph 与 wave-ready 调度
- `frontend/src/lab`: Harness Lab mission-control workbench

## Core Capabilities

- session-first research workflow
- provider-backed intent / reflection with heuristic fallback
- layered context blocks with token-budget trimming
- natural-language constraints with deny-before-allow verdicts
- fixed prompt frame ordering
- replayable execution traces, approval chain, and artifact indexing
- artifact storage with local filesystem and S3-compatible backends
- lease-driven remote-worker protocol with mission / attempt / lease visibility
- pluggable sandbox execution backend through `SandboxExecutor`
  - `docker` default backend with hardened container settings
  - `microvm` real local runner backend with readiness probes and VM trace metadata
  - `microvm_stub` fallback backend for compatibility checks and tests
- role-aware mission orchestration with handoff packets and review verdicts
- offline replay / benchmark evaluation and strict candidate publish gate
- multi-agent self-improvement loop that diagnoses traces, generates candidates, and runs replay / benchmark checks

## Repository Layout

```text
backend/
  app/harness_lab/
    boundary/
    control_plane/
    context/
    constraints/
    fleet/
    improvement/
    orchestrator/
    runtime/
    types/
  tests/
frontend/
docker/
```

## Requirements

- Python 3.11+
- Node.js 18+
- Docker and Docker Compose
- Postgres and Redis for runtime state

## Local Development

### 1. Start infrastructure

```bash
docker compose -f docker/docker-compose.yml up -d harness-lab-postgres harness-lab-redis harness-lab-minio
```

The full compose stack also brings up the backend and frontend:

```bash
docker compose -f docker/docker-compose.yml up -d
```

### 2. Configure environment

At minimum, set these variables:

```bash
export HARNESS_DB_URL=postgresql://harness_lab:harness_lab@127.0.0.1:5432/harness_lab
export HARNESS_REDIS_URL=redis://127.0.0.1:6379/0
```

Optional but commonly used:

```bash
export HARNESS_SANDBOX_BACKEND=docker
export HARNESS_ARTIFACT_BACKEND=local
export HARNESS_ARTIFACT_ROOT=backend/data/harness_lab/artifacts
export HARNESS_WORKER_POLL_INTERVAL=1.0
```

For the local MicroVM runner:

```bash
export HARNESS_SANDBOX_BACKEND=microvm
export HARNESS_MICROVM_BINARY=python3
export HARNESS_MICROVM_KERNEL_IMAGE=backend/data/harness_lab/microvm/vmlinux.bin
export HARNESS_MICROVM_ROOTFS_IMAGE=backend/data/harness_lab/microvm/rootfs.img
export HARNESS_MICROVM_WORKDIR=backend/data/harness_lab/microvm/workdir
```

### 3. Run the backend

```bash
python3 -m backend.app.main
```

The API listens on `http://localhost:4600` and exposes docs at `http://localhost:4600/docs`.

You can also use the CLI entrypoint:

```bash
python3 -m backend.app.harness_lab.cli serve
```

### 4. Run the frontend

```bash
cd frontend
npm install
npm run dev
```

The workbench opens at `http://localhost:3000`.

### 5. Run a worker daemon

```bash
python3 -m backend.app.harness_lab.cli worker serve --label cli-worker
```

## Useful CLI Commands

```bash
python3 -m backend.app.harness_lab.cli doctor
python3 -m backend.app.harness_lab.cli sandbox probe
python3 -m backend.app.harness_lab.cli sandbox backend
python3 -m backend.app.harness_lab.cli queue inspect
python3 -m backend.app.harness_lab.cli leases
python3 -m backend.app.harness_lab.cli workers
python3 -m backend.app.harness_lab.cli eval --suite replay
```

## Test Matrix

```bash
# Backend regression
pytest backend/tests -q

# Focused sandbox + platform regression
pytest backend/tests/unit/test_sandbox_hardening.py \
  backend/tests/unit/test_microvm_executor.py \
  backend/tests/test_harness_lab_platform.py -q

# Integration checks
pytest backend/tests/integration -q
```

Frontend checks:

```bash
cd frontend
npm run build
npm run lint
npm test
```

## Current Limits

- single-user local control plane only
- runtime state requires Postgres + Redis; SQLite is no longer supported for runtime state
- sandboxing supports `docker`, `microvm`, and `microvm_stub`, but the real `microvm` backend is still a local runner rather than a full multi-tenant VM fabric
- semantic constraints are governed through a dedicated engine with deny-before-allow verdicts, but the authoring and rollout UX can still be deepened
- multi-agent orchestration is role-aware and replayable, but still workflow-bounded rather than fully autonomous
- self-improvement currently optimizes policy/workflow versions rather than the platform source tree itself

## See Also

- [Maintenance notes](MAINTENANCE.md)
- [Frontend README](frontend/README.md)
