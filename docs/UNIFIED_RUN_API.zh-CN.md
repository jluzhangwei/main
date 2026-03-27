# 统一 Run API 接入说明

## 目标

统一对外接入入口，避免第三方再区分：

- 单设备会话 `/v1/sessions`
- 多设备任务 `/v2/jobs`

推荐所有新的第三方调用都使用统一入口：

- `POST /api/runs`
- `POST /api/runs/{runId}/messages`
- `GET /api/runs`
- `GET /api/runs/{runId}`
- `GET /api/runs/{runId}/events`
- `GET /api/runs/{runId}/timeline`
- `GET /api/runs/{runId}/report`
- `POST /api/runs/{runId}/actions/approve`
- `POST /api/runs/{runId}/actions/reject`
- `POST /api/runs/{runId}/stop`

## 设计逻辑

- `devices` 只有 1 台：按单设备诊断/查询/配置执行
- `devices` 大于 1 台：按多设备协同分析执行
- 第三方不需要再区分 `session` 和 `job`
- `/v1`、`/v2` 仍保留，但作为兼容接口
- `POST /api/runs/{runId}/messages` 当前用于单设备 Run 的后续对话

## 鉴权

统一 Run API 使用：

- `X-API-Key: <YOUR_API_KEY>`

管理员 Key 仍通过 `/v2/keys` 管理，因为安全与权限标签体系当前仍复用 `/v2` 的能力。

首次初始化管理员 Key：

```bash
curl -sS -X POST 'http://127.0.0.1:8000/v2/keys' \
  -H 'Content-Type: application/json' \
  -d '{"name":"admin","permissions":["*"]}'
```

## 创建 Run

### 单设备

```bash
curl -sS -X POST 'http://127.0.0.1:8000/api/runs' \
  -H 'X-API-Key: <YOUR_API_KEY>' \
  -H 'Content-Type: application/json' \
  -d '{
    "problem": "帮我检查设备版本",
    "operation_mode": "query",
    "automation_level": "read_only",
    "devices": [
      {
        "host": "192.168.0.88",
        "protocol": "ssh",
        "username": "zhangwei",
        "password": "Huawei@123"
      }
    ]
  }'
```

### 多设备协同

```bash
curl -sS -X POST 'http://127.0.0.1:8000/api/runs' \
  -H 'X-API-Key: <YOUR_API_KEY>' \
  -H 'Idempotency-Key: run-20260327-001' \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "OSPF 闪断联合排查",
    "problem": "查一下上次 OSPF 闪断的原因",
    "operation_mode": "diagnosis",
    "automation_level": "assisted",
    "max_gap_seconds": 300,
    "topology_mode": "hybrid",
    "max_device_concurrency": 20,
    "devices": [
      {
        "host": "192.168.0.83",
        "protocol": "ssh",
        "username": "zhangwei",
        "password": "Huawei@123"
      },
      {
        "host": "192.168.0.84",
        "protocol": "ssh",
        "username": "zhangwei",
        "password": "Huawei@123"
      }
    ]
  }'
```

## Run 状态

统一状态：

- `open`
- `running`
- `waiting_approval`
- `completed`
- `failed`
- `cancelled`

说明：

- 多设备底层如果出现 `queued` / `executing`，统一入口会自动归一化到 `open` / `running`
- 第三方不需要再感知内部版本差异

## 查询 Run

```bash
curl -sS -H 'X-API-Key: <YOUR_API_KEY>' \
  'http://127.0.0.1:8000/api/runs/<RUN_ID>'
```

返回核心字段：

- `id`
- `source_id`
- `kind`
- `status`
- `phase`
- `device_count`
- `device_hosts`
- `pending_actions`

## 单设备继续提问

```bash
curl -N -X POST 'http://127.0.0.1:8000/api/runs/<RUN_ID>/messages' \
  -H 'X-API-Key: <YOUR_API_KEY>' \
  -H 'Content-Type: application/json' \
  -d '{"content":"请继续检查接口状态并给出下一步"}'
```

## 列表查询

```bash
curl -sS -H 'X-API-Key: <YOUR_API_KEY>' \
  'http://127.0.0.1:8000/api/runs?offset=0&limit=20'
```

可选过滤：

- `kind=single`
- `kind=multi`

## 统一事件流（SSE）

```bash
curl -N -H 'X-API-Key: <YOUR_API_KEY>' \
  'http://127.0.0.1:8000/api/runs/<RUN_ID>/events?from_seq=0'
```

说明：

- 单设备、多设备都走同一个事件流入口
- 事件名统一为：
  - `trace_step`
  - `completed`
  - `ping`
- `trace_step` 的数据体就是规范化后的步骤对象，包含：
  - `seq_no`
  - `step_type`
  - `title`
  - `status`
  - `detail`
  - `detail_payload`

## 审批命令组

当统一 Run 进入 `waiting_approval` 时：

- 单设备：会批准当前待确认命令批次
- 多设备：会批准当前待确认命令组

批准：

```bash
curl -sS -X POST 'http://127.0.0.1:8000/api/runs/<RUN_ID>/actions/approve' \
  -H 'X-API-Key: <YOUR_API_KEY>' \
  -H 'Content-Type: application/json' \
  -d '{"reason":"approved by operator"}'
```

拒绝：

```bash
curl -sS -X POST 'http://127.0.0.1:8000/api/runs/<RUN_ID>/actions/reject' \
  -H 'X-API-Key: <YOUR_API_KEY>' \
  -H 'Content-Type: application/json' \
  -d '{"reason":"rejected by operator"}'
```

可选字段：

- `item_ids`: 只处理指定命令批次 / 命令组

## 时间线与报告

### 时间线

```bash
curl -sS -H 'X-API-Key: <YOUR_API_KEY>' \
  'http://127.0.0.1:8000/api/runs/<RUN_ID>/timeline'
```

### 报告

```bash
curl -sS -H 'X-API-Key: <YOUR_API_KEY>' \
  'http://127.0.0.1:8000/api/runs/<RUN_ID>/report?format=json'

curl -sS -H 'X-API-Key: <YOUR_API_KEY>' \
  'http://127.0.0.1:8000/api/runs/<RUN_ID>/report?format=markdown'
```

## 停止 Run

```bash
curl -sS -X POST 'http://127.0.0.1:8000/api/runs/<RUN_ID>/stop' \
  -H 'X-API-Key: <YOUR_API_KEY>'
```

## Python 小客户端

仓库已提供统一入口小客户端：

`backend/scripts/unified_diag_client.py`

示例：

```bash
cd backend
. .venv/bin/activate
python scripts/unified_diag_client.py \
  --base-url http://127.0.0.1:8000 \
  --hosts "192.168.0.83 192.168.0.84" \
  --username zhangwei \
  --password 'Huawei@123' \
  --question '查一下上次 OSPF 闪断的原因' \
  --api-key <YOUR_API_KEY> \
  --auto-approve
```

## 兼容策略

- 新接入：优先用 `/api/runs`
- 旧前端或旧脚本：可继续使用 `/v1` 与 `/v2`
- 后续重构方向：统一内核，版本 API 仅保留兼容层
