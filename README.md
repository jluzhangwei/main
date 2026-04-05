# NetOps AI V2

对话式网络设备故障排查平台（Web UI）。

## 功能

- 对话驱动故障诊断（连通性 / 接口 / 路由）
- 设备接入支持 SSH CLI / Telnet CLI / API 适配器
- 命令风险分级（低/中/高）
- 自动化等级开关：只读 / 半自动 / 全自动
- 高风险命令确认弹窗与审计时间线
- 会话导出（Markdown）
- V3 多设备并发任务编排（/v2 异步任务 API）
- API Key 鉴权 + 权限标签（轻量 RBAC）
- 多设备时间聚类 + 拓扑因果边 + 根因候选输出
- 命令能力画像（版本级成功率/错误/替代命中）

## 用户文档

- 图文用户说明书（中文）：[`docs/USER_GUIDE.zh-CN.md`](docs/USER_GUIDE.zh-CN.md)
- Docker 部署说明：[`docs/DOCKER_DEPLOY.zh-CN.md`](docs/DOCKER_DEPLOY.zh-CN.md)
- 统一 Run API 接入说明：[`docs/UNIFIED_RUN_API.zh-CN.md`](docs/UNIFIED_RUN_API.zh-CN.md)
- 主链与分支判定说明：[`docs/MAINLINE_BRANCH_LOGIC.zh-CN.md`](docs/MAINLINE_BRANCH_LOGIC.zh-CN.md)
  - 该文档明确约束：AI 主诊断链仅接收基线、原始命令/回显与执行状态，不接收回显派生解释字段
- V3 运维说明（多设备任务编排）：[`docs/V3_OPERATIONS.zh-CN.md`](docs/V3_OPERATIONS.zh-CN.md)
- V3 对外 API 接入说明：[`docs/V3_API_EXTERNAL.zh-CN.md`](docs/V3_API_EXTERNAL.zh-CN.md)
- V4 预研计划：[`docs/V4_RESEARCH_PLAN.zh-CN.md`](docs/V4_RESEARCH_PLAN.zh-CN.md)

## 快速启动

### 后端

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
uvicorn app.main:app --reload --port 8000
```

### 前端

```bash
cd frontend
npm install
npm run dev
```

### Docker

```bash
docker compose up --build
```

也可以直接用项目自带脚本：

```bash
./docker_netops.sh start
```

启动后：

- 前端：[http://127.0.0.1:5173](http://127.0.0.1:5173)
- 后端 OpenAPI：[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

说明：

- Docker 部署会持久化运行状态到 Docker volume `netops_data`
- 详细操作请看 [`docs/DOCKER_DEPLOY.zh-CN.md`](docs/DOCKER_DEPLOY.zh-CN.md)

## API

### 统一入口（推荐）

- `POST /api/runs`
- `POST /api/runs/{runId}/messages`
- `GET /api/runs`
- `GET /api/runs/{runId}`
- `GET /api/runs/{runId}/events` (SSE)
- `GET /api/runs/{runId}/timeline`
- `GET /api/runs/{runId}/report?format=json|markdown|pdf`
- `POST /api/runs/{runId}/actions/approve`
- `POST /api/runs/{runId}/actions/reject`
- `POST /api/runs/{runId}/stop`

说明：

- `devices` 只有 1 台时，统一入口会落到单设备会话能力。
- `devices` 大于 1 台时，统一入口会落到多设备协同能力。
- `POST /api/runs/{runId}/messages` 当前用于单设备 Run 的后续对话。
- `/v1` 与 `/v2` 继续保留，但定位为兼容接口，不再推荐作为新的第三方接入入口。

### 收尾状态（当前维护约定）

- 前端主工作台主链已统一到 `/api/runs`
- `timeline / trace / export / events` 已统一走 unified run service
- `/v1`、`/v2` 继续保留，但主要用于兼容已有脚本、测试与管理接口
- 历史旧任务若当时未写入原生 trace，仍会走有限的 legacy 映射；新任务/新会话优先使用原生 trace

### v1（兼容接口）

- `POST /v1/sessions`
- `PATCH /v1/sessions/{id}`
- `POST /v1/sessions/{id}/messages` (SSE)
- `POST /v1/sessions/{id}/commands/{cmdId}/confirm`
- `GET /v1/sessions/{id}/timeline`
- `POST /v1/sessions/{id}/export`

### v2（兼容接口 / 多设备任务）

- `POST /v2/keys`
- `GET /v2/keys`
- `DELETE /v2/keys/{id}`
- `POST /v2/jobs`
- `GET /v2/jobs`
- `GET /v2/jobs/query`（分页与过滤）
- `GET /v2/jobs/{jobId}`
- `POST /v2/jobs/{jobId}/cancel`
- `GET /v2/jobs/{jobId}/events` (SSE)
- `POST /v2/jobs/{jobId}/actions/{actionGroupId}/approve`
- `POST /v2/jobs/{jobId}/actions/{actionGroupId}/reject`
- `GET /v2/jobs/{jobId}/timeline`
- `GET /v2/jobs/{jobId}/report?format=json|markdown|pdf`
- `GET /v2/audit/logs`
- `GET /v2/audit/reports`
- `GET /v2/command-profiles`
- `GET /v2/security/permission-templates`

## 统一 Run API 快速调用示例

### 1) 初始化管理员 Key（首次可无鉴权）

```bash
curl -sS -X POST 'http://127.0.0.1:8000/v2/keys' \
  -H 'Content-Type: application/json' \
  -d '{"name":"admin","permissions":["*"]}'
```

### 2) 创建统一 Run（单设备）

注意：

- `X-API-Key` 要填真实 Key，本身不要带 `< >`
- JSON 体内所有引号都必须是英文双引号 `"`

```bash
curl -sS -X POST 'http://127.0.0.1:8000/api/runs' \
  -H 'X-API-Key: your_real_key_here' \
  -H 'Content-Type: application/json' \
  -d '{
    "problem": "帮我检查最近一次 OSPF 闪断原因",
    "operation_mode": "diagnosis",
    "automation_level": "assisted",
    "devices": [
      {"host":"192.168.0.88","protocol":"ssh","username":"zhangwei","password":"Huawei@123"}
    ]
  }'
```

### 3) 创建统一 Run（多设备协同）

```bash
curl -sS -X POST 'http://127.0.0.1:8000/api/runs' \
  -H 'X-API-Key: your_real_key_here' \
  -H 'Idempotency-Key: job-20260325-rca-001' \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "跨设备故障分析",
    "problem": "5分钟内多设备告警，定位根因和传播链",
    "operation_mode": "diagnosis",
    "automation_level": "assisted",
    "max_gap_seconds": 300,
    "topology_mode": "hybrid",
    "max_device_concurrency": 20,
    "webhook_url": "http://your-system.example.com/netops/events",
    "webhook_events": ["job_completed","job_failed","job_cancelled","action_group_completed"],
    "devices": [
      {"host":"192.168.0.88","protocol":"ssh","username":"zhangwei","password":"Huawei@123"},
      {"host":"192.168.0.101","protocol":"ssh","username":"zhangwei","password":"Admin@123"},
      {"host":"192.168.0.102","protocol":"ssh","username":"zhangwei","password":"Admin@123"}
    ]
  }'
```

### 4) 查询统一 Run 状态

```bash
curl -sS -H 'X-API-Key: <YOUR_API_KEY>' \
  'http://127.0.0.1:8000/api/runs/<RUN_ID>'
```

### 5) 单设备继续提问

```bash
curl -N -X POST 'http://127.0.0.1:8000/api/runs/<RUN_ID>/messages' \
  -H 'X-API-Key: <YOUR_API_KEY>' \
  -H 'Content-Type: application/json' \
  -d '{"content":"请继续检查接口状态并给出下一步"}'
```

### 6) 审批待执行命令组

```bash
curl -sS -X POST 'http://127.0.0.1:8000/api/runs/<RUN_ID>/actions/approve' \
  -H 'X-API-Key: <YOUR_API_KEY>' \
  -H 'Content-Type: application/json' \
  -d '{"reason":"approved by operator"}'
```

### 7) 获取统一 Run 时间线 / 报告

```bash
curl -sS -H 'X-API-Key: <YOUR_API_KEY>' \
  'http://127.0.0.1:8000/api/runs/<RUN_ID>/timeline'

curl -sS -H 'X-API-Key: <YOUR_API_KEY>' \
  'http://127.0.0.1:8000/api/runs/<RUN_ID>/report?format=markdown'
```

### 8) 分页查询历史 Run

```bash
curl -sS -H 'X-API-Key: <YOUR_API_KEY>' \
  'http://127.0.0.1:8000/api/runs?offset=0&limit=20'
```

## 权限标签建议

- `job.read`: 读取任务、时间线、报告、事件
- `job.write`: 创建任务
- `command.execute`: 运行修复类任务（`mode=repair` 需此权限）
- `command.approve`: 审批/拒绝命令组
- `policy.write`: 管理 API Key / 策略
- `audit.read`: 审计日志与报表读取

说明：内置前端 UI 默认可走受信任通道访问 `/v2`（可不填 API Key）。如需严格要求所有调用都使用 API Key，可设置：

```bash
export NETOPS_UI_TRUSTED_V2_BYPASS=false
```

## 单次设备诊断

使用后端脚本可以直接对真实设备做一次排障诊断，密码只通过环境变量传入，不要写进仓库。

```bash
cd backend
export DEVICE_HOST=192.168.0.88
export DEVICE_USERNAME=zhangwei
export DEVICE_PASSWORD
read -s DEVICE_PASSWORD
export DEVICE_PROTOCOL=ssh
export VENDOR=huawei
export AUTOMATION_LEVEL=assisted
export DIAG_MESSAGE='请帮我诊断连通性、接口和路由问题'
python scripts/run_device_diag.py
```

也可以直接使用统一入口脚本：

```bash
cd backend
. .venv/bin/activate
python scripts/unified_diag_client.py \
  --base-url http://127.0.0.1:8000 \
  --host 192.168.0.88 \
  --host 192.168.0.101 \
  --username zhangwei \
  --password 'Huawei@123' \
  --problem '查一下上次 OSPF 闪断原因' \
  --api-key your_real_key_here \
  --auto-approve
```

## 统一 Run 压测脚本

```bash
cd backend
. .venv/bin/activate
python scripts/v2_stress_jobs.py --base-url http://127.0.0.1:8000 --api-key <ADMIN_KEY> --jobs 30 --device-count 8
```

## 统一 Run 回归脚本

```bash
cd backend
. .venv/bin/activate
python scripts/v3_regression_runner.py \
  --base-url http://127.0.0.1:8000 \
  --api-key <ADMIN_KEY> \
  --scenario scripts/scenarios/multi_device_rca.sample.json
```
