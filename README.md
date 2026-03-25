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

## API

### v1（保持兼容）

- `POST /v1/sessions`
- `PATCH /v1/sessions/{id}`
- `POST /v1/sessions/{id}/messages` (SSE)
- `POST /v1/sessions/{id}/commands/{cmdId}/confirm`
- `GET /v1/sessions/{id}/timeline`
- `POST /v1/sessions/{id}/export`

### v2（多设备任务）

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

## v2 快速调用示例

### 1) 初始化管理员 Key（首次可无鉴权）

```bash
curl -sS -X POST 'http://127.0.0.1:8000/v2/keys' \
  -H 'Content-Type: application/json' \
  -d '{"name":"admin","permissions":["*"]}'
```

### 2) 创建多设备任务（诊断模式）

```bash
curl -sS -X POST 'http://127.0.0.1:8000/v2/jobs' \
  -H 'X-API-Key: <YOUR_API_KEY>' \
  -H 'Idempotency-Key: job-20260325-rca-001' \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "跨设备故障分析",
    "problem": "5分钟内多设备告警，定位根因和传播链",
    "mode": "diagnosis",
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

### 3) 订阅事件流

```bash
curl -N -H 'X-API-Key: <YOUR_API_KEY>' \
  'http://127.0.0.1:8000/v2/jobs/<JOB_ID>/events?from_seq=0'
```

### 4) 获取报告

```bash
curl -sS -H 'X-API-Key: <YOUR_API_KEY>' \
  'http://127.0.0.1:8000/v2/jobs/<JOB_ID>/report?format=markdown'
```

### 5) 分页查询任务

```bash
curl -sS -H 'X-API-Key: <YOUR_API_KEY>' \
  'http://127.0.0.1:8000/v2/jobs/query?offset=0&limit=20&status=completed'
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

## V3 压测脚本

```bash
cd backend
. .venv/bin/activate
python scripts/v2_stress_jobs.py --base-url http://127.0.0.1:8000 --api-key <ADMIN_KEY> --jobs 30 --device-count 8
```

## V3 回归脚本

```bash
cd backend
. .venv/bin/activate
python scripts/v3_regression_runner.py \
  --base-url http://127.0.0.1:8000 \
  --api-key <ADMIN_KEY> \
  --scenario scripts/scenarios/multi_device_rca.sample.json
```
