# Harness Lab

Harness Lab 是一个研究优先、可回放、面向生产化演进的多 agent 平台。它不是旧工作流产品，也还不是最终形态的 agent 云，但当前已经具备一条完整的控制平面、执行边界、约束治理和前端工作台链路。

## 核心特性

| 特性 | 说明 |
|------|------|
| **Session-first 工作流** | 以 session 为核心的研究型工作流 |
| **分层 Context 管理** | Token-budget 分层上下文裁剪 |
| **自然语言约束** | deny-before-allow verdict 约束治理 |
| **可回放执行追踪** | Approval chain、artifact indexing |
| **多存储后端** | 本地文件系统 + S3/MinIO |
| **远程 Worker 协议** | Lease-driven polling + heartbeat |
| **可插拔 Sandbox** | Docker (生产) / MicroVM (VM 隔离) / Stub (测试) |
| **自我改进循环** | Trace 诊断 → Candidate 生成 → Benchmark 验证 |
| **SOCKS5 安全代理** | Worker 远程通信安全隧道 |
| **Terminal UI (TUI)** | Textual 可视化管理界面 (新增) |
| **Worker Auto-Pair** | 自动角色检测与配置 (新增) |
| **Systemd 服务** | 生产级 systemd 部署 (新增) |
| **一键 Bootstrap** | 完整基础设施部署脚本 (新增) |

---

## 架构设计

### 核心模块

```
backend/app/harness_lab/
├── context/          # 分层 context 管理
├── constraints/      # 自然语言约束解析、编译与 policy verdict
├── boundary/         # Sandbox 执行边界、patch staging、workspace 审计
├── fleet/            # Worker、Lease、Dispatch 协议和适配层
├── runtime/          # Session / Run / Mission / Attempt / Lease runtime
├── improvement/      # Candidate、Eval harness、Publish gate、Canary
├── control_plane/    # Sessions、Runs、Workers、Leases API
├── orchestrator/     # Task graph 与 wave-ready 调度
├── knowledge/        # 知识库索引与搜索
├── workers/          # WorkerRuntimeClient + SOCKS5 支持
└── types/            # 类型定义
```

### Sandbox 执行后端

| 后端 | 用途 | 特点 |
|------|------|------|
| `docker` | 生产级默认 | 硬化容器设置，rootless 支持 |
| `microvm` | VM 风格隔离 | 真正的本地 VM runner，readiness probes |
| `microvm_stub` | 测试兼容 | 不启动真实 VM，用于 CI/CD |

### Artifact 存储后端

| 后端 | 用途 | 配置 |
|------|------|------|
| `local` | 本地文件系统 | `HARNESS_ARTIFACT_ROOT` |
| `s3` | S3/MinIO 对象存储 | `HARNESS_S3_ENDPOINT`, `HARNESS_S3_BUCKET` |

### Worker 远程执行协议

```
Control Plane ←── Lease Polling ──→ Worker
                │
                ├── Heartbeat (健康检查)
                ├── Dispatch (任务分发)
                └── Status Update (状态同步)
```

---

## 快速开始

### 一键部署（推荐）

```bash
# Control Plane 完整部署（PostgreSQL + Redis + Docker + API）
curl -sSL https://raw.githubusercontent.com/3452808350-max/Harness-Lab/main/scripts/bootstrap-control-plane.sh | bash

# 或本地运行
./scripts/bootstrap-control-plane.sh --port 4600 --with-worker
```

### Systemd 服务管理

```bash
# 安装 systemd 服务（开机自启）
./scripts/install-systemd.sh

# 管理命令
sudo systemctl start harness-lab    # 启动
sudo systemctl stop harness-lab     # 停止
sudo systemctl status harness-lab   # 状态
sudo journalctl -u harness-lab -f   # 日志
```

### Terminal UI (TUI)

```bash
# 启动可视化管理界面
hlab tui control [--port 4600] [--theme dark|light]

# Worker 详情页
hlab tui worker [--worker-id ID]

# 快速状态检查
hlab tui status [--live]
```

**TUI 快捷键：**

| 按键 | 功能 |
|------|------|
| `i` | 查看选中 Worker 详情 |
| `d` | Drain Worker |
| `r` | Resume Worker |
| `l` | 切换 Leases 显示 |
| `h` | 强制 Heartbeat |
| `q` | 退出 |

### Worker Auto-Pair

```bash
# 自动检测角色并配置
hlab worker auto-pair --role executor --label my-worker

# 支持角色模板
# - general: 通用 Worker
# - executor: 执行 Worker
# - reviewer: 审核 Worker
# - planner: 规划 Worker
```

---

## 安装与配置

### 系统需求

- Python 3.11+
- Node.js 18+
- Docker + Docker Compose
- PostgreSQL + Redis

### 1. 启动基础设施

```bash
docker compose -f docker/docker-compose.yml up -d harness-lab-postgres harness-lab-redis harness-lab-minio
```

完整栈启动：

```bash
docker compose -f docker/docker-compose.yml up -d
```

### 2. 环境配置

**必需变量：**

```bash
export HARNESS_DB_URL=postgresql://harness_lab:harness_lab@127.0.0.1:5432/harness_lab
export HARNESS_REDIS_URL=redis://127.0.0.1:6379/0
```

**常用配置：**

```bash
export HARNESS_SANDBOX_BACKEND=docker
export HARNESS_ARTIFACT_BACKEND=local
export HARNESS_ARTIFACT_ROOT=backend/data/harness_lab/artifacts
export HARNESS_WORKER_POLL_INTERVAL=1.0
```

**模型配置：**

```bash
export HARNESS_LAB_MODEL_PROVIDER=deepseek
export HARNESS_LAB_MODEL_NAME=deepseek-chat
```

### 3. SOCKS5 代理配置 (新增)

Worker 与 Control Plane 的远程安全通信支持 SOCKS5 代理：

```bash
export HARNESS_SOCKS5_PROXY_HOST=127.0.0.1
export HARNESS_SOCKS5_PROXY_PORT=1080
export HARNESS_SOCKS5_PROXY_USER=username   # 可选
export HARNESS_SOCKS5_PROXY_PASS=password   # 可选
```

安装可选依赖：

```bash
pip install pysocks
```

**HTTPS Control Plane（可选）：**

```bash
export HARNESS_USE_HTTPS=true
export HARNESS_CONTROL_PLANE_URL=https://your-control-plane.example.com
```

### 4. Sandbox 安全配置

```bash
export HARNESS_SANDBOX_ROOTLESS_USER=1000:1000
export HARNESS_SANDBOX_NO_NEW_PRIVILEGES=true
export HARNESS_SANDBOX_CAP_DROP_ALL=true
```

### 5. 运行服务

**后端：**

```bash
python3 -m backend.app.main
# API: http://localhost:4600
# Docs: http://localhost:4600/docs
```

**前端：**

```bash
cd frontend
npm install
npm run dev
# Workbench: http://localhost:3000
```

---

## CLI 命令参考

### TUI 命令 (新增)

| 命令 | 说明 |
|------|------|
| `hlab tui control` | 启动 Control Plane TUI Dashboard |
| `hlab tui worker [--worker-id ID]` | Worker 详情页 |
| `hlab tui status [--live]` | 快速状态检查（非交互） |

### Systemd 命令 (新增)

| 命令 | 说明 |
|------|------|
| `sudo systemctl start harness-lab` | 启动服务 |
| `sudo systemctl stop harness-lab` | 停止服务 |
| `sudo systemctl status harness-lab` | 查看状态 |
| `sudo systemctl restart harness-lab` | 重启服务 |
| `sudo journalctl -u harness-lab -f` | 实时日志 |

### Bootstrap 命令 (新增)

| 命令 | 说明 |
|------|------|
| `./scripts/bootstrap-control-plane.sh` | Control Plane 完整部署 |
| `./scripts/bootstrap-worker.sh` | Worker 远程部署 |
| `./scripts/install-systemd.sh` | Systemd 服务安装 |
| `./scripts/update.sh [--check]` | 安全更新脚本 |
| `./scripts/uninstall.sh [--keep-data]` | 卸载脚本 |

### 基础命令

| 命令 | 说明 |
|------|------|
| `hlab doctor` | 系统健康检查 |
| `hlab fleet` | Worker 集群状态 |
| `hlab queue inspect` | 任务队列检查 |

### Sandbox 命令

| 命令 | 说明 |
|------|------|
| `hlab sandbox probe` | Sandbox 后端探测 |
| `hlab sandbox backend` | 当前后端状态 |

### Session/Run 命令

| 命令 | 说明 |
|------|------|
| `hlab submit <goal>` | 创建 session 并执行 run |
| `hlab runs watch [--run-id ID]` | 监控 run 执行状态 |
| `hlab runs list` | 列出所有 runs |
| `hlab replays <run-id>` | 回放执行追踪 |

### Worker 命令

| 命令 | 说明 |
|------|------|
| `hlab worker auto-pair [--role ROLE]` | 自动角色检测与配置 (新增) |
| `hlab worker register [--label NAME]` | 注册新 Worker |
| `hlab worker status <worker-id>` | Worker 详细状态 |
| `hlab worker drain <worker-id>` | 暂停 Worker 任务 |
| `hlab worker resume <worker-id>` | 恢复 Worker |
| `hlab worker serve [--once]` | 启动 Worker daemon |

**远程 Worker（含 SOCKS5）：**

```bash
export HARNESS_SOCKS5_PROXY_HOST=proxy.example.com
export HARNESS_SOCKS5_PROXY_PORT=1080

hlab worker serve \
  --control-plane-url http://remote-control-plane:4600 \
  --label remote-worker
```

### Knowledge 命令

| 命令 | 说明 |
|------|------|
| `hlab knowledge reindex [--scope SCOPE]` | 知识库重新索引 |
| `hlab knowledge search <query>` | 知识库搜索 |

### Canary/Improvement 命令

| 命令 | 说明 |
|------|------|
| `hlab canary start <policy-id>` | 启动 Canary rollout |
| `hlab canary status` | Canary 状态 |
| `hlab canary promote` | 推进 Canary |
| `hlab canary analyze` | 分析 Canary 结果 |
| `hlab candidates` | 列出改进候选 |
| `hlab promote <candidate-id>` | 发布候选 |
| `hlab rollback <candidate-id>` | 回滚候选 |

### Eval 命令

| 命令 | 说明 |
|------|------|
| `hlab eval --suite replay` | 回放评估 |
| `hlab eval --suite benchmark` | Benchmark 评估 |

---

## 使用示例

### 端到端工作流

```bash
# 1. 检查系统状态
hlab doctor

# 2. 启动 Worker
hlab worker serve --label local-worker

# 3. 提交任务
hlab submit "分析 backend/app/harness_lab 的架构设计"

# 4. 监控执行
hlab runs watch

# 5. 回放追踪
hlab replays <run-id>
```

### 远程 Worker 示例

```bash
# 配置 SOCKS5 代理
export HARNESS_SOCKS5_PROXY_HOST=192.168.1.100
export HARNESS_SOCKS5_PROXY_PORT=1080

# 连接远程 Control Plane
hlab worker serve \
  --control-plane-url http://control-plane.example.com:4600 \
  --label remote-worker-01 \
  --capabilities code,analysis

# 查看远程 Worker 状态
hlab worker status <worker-id> --control-plane-url http://control-plane.example.com:4600
```

### Canary Rollout 示例

```bash
# 启动 Canary（10% 流量）
hlab canary start policy-v2 --scope percentage --value 10

# 监控 Canary
hlab canary status

# 分析结果
hlab canary analyze

# 推进或回滚
hlab canary promote  # 100% 流量
hlab rollback <candidate-id>  # 回滚
```

---

## 测试

### 后端测试

```bash
# 全量回归
pytest backend/tests -q

# Sandbox + Platform
pytest backend/tests/unit/test_sandbox_hardening.py \
  backend/tests/unit/test_microvm_executor.py \
  backend/tests/test_harness_lab_platform.py -q

# 集成测试
pytest backend/tests/integration -q
```

### 前端测试

```bash
cd frontend
npm run build
npm run lint
npm test
```

---

## 当前限制

- 单用户本地 Control Plane
- 运行时状态需要 PostgreSQL + Redis（不再支持 SQLite）
- MicroVM 后端仍是本地 runner，非多租户 VM fabric
- 语义约束通过 deny-before-allow engine，但 authoring UX 待深化
- 多 Agent orchestration 仍为 workflow-bounded，非完全自主
- 自我改进目前优化 policy/workflow 版本，而非平台源码本身

---

## 文档索引

| 文档 | 说明 |
|------|------|
| [MAINTENANCE.md](MAINTENANCE.md) | 维护笔记 |
| [frontend/README.md](frontend/README.md) | 前端文档 |

---

## License

MIT License

---

*Harness Lab - 研究优先、可回放、面向生产化演进的多 Agent 平台*