# Harness Lab 快速上手指南

一个多 Agent 任务执行平台。控制平面管理任务队列，Worker 执行任务。

---

## 一分钟理解

```
┌─────────────────┐     ┌─────────────────┐
│  Control Plane  │────▶│    Worker 1     │
│  (任务调度中心)  │     │  (执行任务)     │
│  PostgreSQL     │     ┌─────────────────┘
│  Redis          │────▶│    Worker 2     │
│  FastAPI        │     │  (执行任务)     │
└─────────────────┘     └─────────────────┘
       服务器 A              服务器 B/C/D...
```

**你需要做的**：
1. 在一台服务器部署 **控制平面**
2. 在其他机器部署 **Worker**（连接控制平面）

---

## 第一步：部署控制平面

在一台服务器上运行：

```bash
curl -sSL https://raw.githubusercontent.com/3452808350-max/Harness-Lab/main/scripts/bootstrap-control-plane.sh | bash
```

安装完成后：
- API 地址：`http://服务器IP:4600`
- 命令：`sudo systemctl status harness-lab`

---

## 第二步：部署 Worker

在其他机器上运行（连接控制平面）：

```bash
curl -sSL https://raw.githubusercontent.com/3452808350-max/Harness-Lab/main/scripts/bootstrap-worker.sh | bash -s -- \
  --control-plane-url http://控制平面IP:4600 \
  --role executor \
  --serve
```

**角色选择**：
| 角色 | 能力 | 用途 |
|------|------|------|
| `general` | 文件、Git、Shell、HTTP | 通用任务 |
| `executor` | + Sandbox 容器隔离 | 代码执行、测试 |
| `reviewer` | 只读权限 | 代码审查 |
| `planner` | 知识搜索、模型推理 | 任务规划 |

---

## 第三步：使用 CLI

### 查看状态

```bash
# 在控制平面服务器上
cd ~/.harness-lab
./venv/bin/hlab status

# 查看所有 worker
./venv/bin/hlab worker list

# 查看任务队列
./venv/bin/hlab task list
```

### 提交任务

```bash
# 提交一个任务
./venv/bin/hlab task submit \
  --description "分析代码仓库" \
  --role executor \
  --priority high

# 批量提交
./venv/bin/hlab task batch tasks.json
```

### 管理 Worker

```bash
# 手动注册 worker
./venv/bin/hlab worker auto-pair --role executor

# 下线维护
./venv/bin/hlab worker drain <worker-id>

# 恢复上线
./venv/bin/hlab worker resume <worker-id>
```

---

## 常用命令速查

```bash
# 服务管理（控制平面）
sudo systemctl start harness-lab    # 启动
sudo systemctl stop harness-lab     # 停止
sudo systemctl restart harness-lab  # 重启
sudo journalctl -u harness-lab -f   # 查看日志

# Worker 管理
hlab worker list                    # 列出所有 worker
hlab worker auto-pair --role executor --dry-run  # 预览配置
hlab worker drain <id>              # 下线维护
hlab worker resume <id>             # 恢复服务

# 任务管理
hlab task list                      # 查看队列
hlab task submit --description "xxx"  # 提交任务
hlab task cancel <task-id>          # 取消任务
```

---

## 配置文件

位置：`~/.harness-lab/.env`

```bash
# 数据库连接（自动生成）
DATABASE_URL=postgresql://harness:xxx@127.0.0.1:5432/harness_lab

# Redis 连接
REDIS_URL=redis://127.0.0.1:6379/0

# API 端口
API_PORT=4600

# Sandbox 后端
SANDBOX_BACKEND=docker  # 或 mock（开发模式）

# 远程 Worker 连接
CONTROL_PLANE_URL=http://控制平面IP:4600

# SOCKS5 代理（可选）
HARNESS_SOCKS5_PROXY_HOST=proxy.example.com
HARNESS_SOCKS5_PROXY_PORT=1080
```

---

## 故障排查

### 控制平面无法启动

```bash
# 检查 PostgreSQL
sudo systemctl status postgresql

# 检查 Redis
sudo systemctl status redis-server

# 检查 Docker
sudo systemctl status docker

# 查看详细日志
sudo journalctl -u harness-lab --since "5 minutes ago"
```

### Worker 连接失败

```bash
# 检查控制平面是否可达
curl http://控制平面IP:4600/health

# 检查 .env 配置
cat ~/.harness-lab/.env | grep CONTROL_PLANE

# 测试连接
./venv/bin/hlab worker ping
```

### 数据库连接失败

```bash
# 检查密码
sudo -u postgres psql -c "ALTER USER harness WITH PASSWORD '新密码';"

# 更新 .env
nano ~/.harness-lab/.env
```

---

## 开发模式

不需要 Docker/Sandbox 的轻量部署：

```bash
# 控制平面开发模式
curl -sSL ... | bash -s -- --dev

# Worker 开发模式
curl -sSL ... | bash -s -- --control-plane-url http://xxx:4600 --role general --serve
```

---

## 目录结构

```
~/.harness-lab/
├── .env              # 配置文件
├── venv/             # Python 环境
├── backend/          # 源代码
│   └── app/
│       └── harness_lab/
│           ├── cli.py        # CLI 命令
│           ├── fleet/        # Worker 管理
│           ├── runtime/      # 任务执行
│           └── control_plane/ # API 路由
└── logs/             # 日志文件
```

---

## 快速回顾

| 你要做什么 | 命令 |
|-----------|------|
| 部署控制平面 | `curl ...bootstrap-control-plane.sh | bash` |
| 部署 Worker | `curl ...bootstrap-worker.sh | bash -s -- --control-plane-url http://IP:4600` |
| 查看服务状态 | `sudo systemctl status harness-lab` |
| 查看所有 Worker | `hlab worker list` |
| 提交任务 | `hlab task submit --description "xxx"` |
| 下线 Worker | `hlab worker drain <id>` |

---

**有问题？**

- 日志：`sudo journalctl -u harness-lab -f`
- 健康检查：`curl http://IP:4600/health`
- 重启服务：`sudo systemctl restart harness-lab`