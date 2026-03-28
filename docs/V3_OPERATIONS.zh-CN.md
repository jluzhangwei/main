# NetOps AI V3 运维说明（多设备协同与兼容运维）

本文档描述多设备协同能力、权限模型、审批流程与压测方法。

当前约定：

- 对外新接入优先使用统一 `/api/runs`
- `/v2` 仍保留，主要承担 API Key、安全、审计与兼容任务接口
- 本文档中的 `/v2` 示例更多用于运维管理与兼容调用

## 1. 能力总览

- 多设备异步任务：`diagnosis / inspection / repair`
- 阶段流转：`Collect -> Correlate -> Plan -> Approve -> Execute -> Analyze -> Conclude`
- 审批粒度：命令组（Action Group）
- 安全模型：API Key + 权限标签（轻量 RBAC）
- 实时事件：`/v2/jobs/{jobId}/events`（SSE）
- 报告导出：JSON / Markdown / PDF

## 2. 权限标签

- `job.read`：读取任务、时间线、报告、事件
- `job.write`：创建/停止任务、更新拓扑与 RCA 权重
- `command.execute`：允许创建 `repair` 模式任务
- `command.approve`：审批/拒绝命令组
- `policy.write`：管理 API Key
- `audit.read`：读取审计日志与报表

说明：内置 Web UI 默认可使用受信任通道调用兼容管理接口（无需手工输入 API Key）；第三方系统接入建议始终使用 API Key，并优先走 `/api/runs`。

## 3. API Key 生命周期

### 3.1 首次引导

首次系统无 key 时可直接创建管理员 key：

```bash
curl -sS -X POST 'http://127.0.0.1:8000/v2/keys' \
  -H 'Content-Type: application/json' \
  -d '{"name":"admin","permissions":["*"]}'
```

### 3.2 更新、轮换、删除

- 更新：`PATCH /v2/keys/{id}`（启停、过期时间、禁用原因）
- 轮换：`POST /v2/keys/{id}/rotate`
- 删除：`DELETE /v2/keys/{id}`

## 4. 多设备任务 API

### 4.0 推荐统一入口

- 创建运行：`POST /api/runs`
- 查询运行：`GET /api/runs/{runId}`
- 订阅事件：`GET /api/runs/{runId}/events`
- 审批：`POST /api/runs/{runId}/actions/approve|reject`
- 获取时间线/报告：`GET /api/runs/{runId}/timeline` / `GET /api/runs/{runId}/report`

说明：

- 当 `devices > 1` 时，统一入口会自动落到多设备协同能力
- `/v2/jobs/*` 主要用于兼容脚本、管理与细粒度运维接口

### 4.1 创建任务

```bash
curl -sS -X POST 'http://127.0.0.1:8000/v2/jobs' \
  -H 'X-API-Key: <KEY>' \
  -H 'Idempotency-Key: v3-job-001' \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "跨设备根因分析",
    "problem": "5分钟内多设备出现异常，定位根因",
    "mode": "diagnosis",
    "topology_mode": "hybrid",
    "max_gap_seconds": 300,
    "max_device_concurrency": 20,
    "execution_policy": "stop_on_failure",
    "devices": [
      {"host":"192.168.0.88","protocol":"ssh","username":"zhangwei","password":"Huawei@123"},
      {"host":"192.168.0.101","protocol":"ssh","username":"zhangwei","password":"Admin@123"},
      {"host":"192.168.0.102","protocol":"ssh","username":"zhangwei","password":"Admin@123"}
    ]
  }'
```

### 4.2 查询与事件

- `GET /v2/jobs/query?offset=0&limit=20&status=running`
- `GET /v2/jobs/{jobId}`
- `GET /v2/jobs/{jobId}/timeline`
- `GET /v2/jobs/{jobId}/events?from_seq=0`

### 4.3 审批流

- 单组通过：`POST /v2/jobs/{jobId}/actions/{groupId}/approve`
- 单组拒绝：`POST /v2/jobs/{jobId}/actions/{groupId}/reject`
- 批量通过：`POST /v2/jobs/{jobId}/actions/approve-batch`
- 批量拒绝：`POST /v2/jobs/{jobId}/actions/reject-batch`

### 4.4 任务中动态调参

- 更新拓扑输入：`PUT /v2/jobs/{jobId}/topology`
- 更新 RCA 权重：`PUT /v2/jobs/{jobId}/rca-weights`
- 停止任务：`POST /v2/jobs/{jobId}/cancel`

## 5. 审计与报表

- 审计日志：`GET /v2/audit/logs`
  - 支持 `action/status/actor_key_id/limit/offset`
- 审计报表：`GET /v2/audit/reports`
  - 支持 `format=json|csv|pdf`

## 6. 前端页面（V3 任务编排）

左侧：
- API Key 管理
- 任务创建（设备 JSON、拓扑 JSON、执行策略）
- 任务列表筛选与选择

右侧：
- 任务详情（阶段/状态/根因）
- 待审批命令组（可批量勾选审批）
- 命令结果与事件流
- 拓扑输入与 RCA 权重在线更新

底部：
- 审计日志
- 命令成功率画像

## 7. 并发压测脚本（统一 /api/runs）

脚本：`backend/scripts/v2_stress_jobs.py`

```bash
cd backend
. .venv/bin/activate
python scripts/v2_stress_jobs.py \
  --base-url http://127.0.0.1:8000 \
  --api-key <ADMIN_KEY> \
  --jobs 30 \
  --device-count 8 \
  --create-concurrency 12 \
  --mode diagnosis
```

输出包括：
- 任务状态分布
- 平均耗时 / P50 / 最大耗时

说明：
- 脚本内部已切换到统一 `/api/runs`。
- `--mode` 现支持 `diagnosis / query / config`，同时兼容旧写法 `inspection / repair`。

## 8. 多设备回归脚本（统一 /api/runs）

脚本：`backend/scripts/v3_regression_runner.py`

```bash
cd backend
. .venv/bin/activate
python scripts/v3_regression_runner.py \
  --base-url http://127.0.0.1:8000 \
  --api-key <ADMIN_KEY> \
  --scenario scripts/scenarios/multi_device_rca.sample.json
```

说明：
- 场景文件优先使用 `operation_mode` 字段。
- 为兼容旧样例，脚本仍接受 `mode=inspection/repair`，并自动映射到统一运行模式。

## 9. 审计报表导出脚本

脚本：`backend/scripts/export_audit_report.py`

```bash
cd backend
. .venv/bin/activate
python scripts/export_audit_report.py \
  --base-url http://127.0.0.1:8000 \
  --api-key <AUDIT_KEY> \
  --format csv
```

## 10. 回归检查建议

- 后端：`cd backend && ./.venv/bin/pytest -q`
- 前端：`cd frontend && npm run build`
