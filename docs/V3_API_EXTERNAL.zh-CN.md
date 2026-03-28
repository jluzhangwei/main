# V3 对外 API 接入说明（统一 `/api/runs`）

本文档用于外部系统将 NetOps AI 作为能力服务接入。

说明：

- 新接入统一使用 `/api/runs`
- `/v1`、`/v2` 仍保留，但属于兼容接口
- API Key 生命周期与权限模板当前仍通过 `/v2/keys`、`/v2/security/permission-templates` 管理

## 1. 鉴权

- 头部：`X-API-Key: <KEY>`
- 首次引导（系统尚无 key）：可直接调用 `POST /v2/keys` 创建管理员 key。
- 内置前端 UI 已支持“免填 API Key”工作模式（通过受信任 UI 头部转发），该模式仅用于同站 UI；第三方系统接入仍建议使用 API Key。

## 2. 核心流程

1. 创建运行：`POST /api/runs`
2. 轮询状态：`GET /api/runs/{runId}` 或 `GET /api/runs`
3. 订阅事件：`GET /api/runs/{runId}/events?from_seq=0`（SSE）
4. 审批命令批次 / 命令组：
   - 通过：`POST /api/runs/{runId}/actions/approve`
   - 拒绝：`POST /api/runs/{runId}/actions/reject`
5. 拉取结果：`GET /api/runs/{runId}/timeline` / `GET /api/runs/{runId}/report`

## 3. 请求示例

### 3.1 创建运行

```bash
curl -sS -X POST 'http://127.0.0.1:8000/api/runs' \
  -H 'X-API-Key: <KEY>' \
  -H 'Idempotency-Key: ext-job-001' \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "external-call-demo",
    "problem": "多设备异常关联分析",
    "operation_mode": "diagnosis",
    "automation_level": "assisted",
    "topology_mode": "hybrid",
    "max_gap_seconds": 300,
    "max_device_concurrency": 20,
    "devices": [
      {"host":"192.168.0.88","protocol":"ssh","username":"zhangwei","password":"Huawei@123"},
      {"host":"192.168.0.102","protocol":"ssh","username":"zhangwei","password":"Admin@123"}
    ]
  }'
```

### 3.2 批量审批 / 统一审批

```bash
curl -sS -X POST 'http://127.0.0.1:8000/api/runs/<RUN_ID>/actions/approve' \
  -H 'X-API-Key: <KEY>' \
  -H 'Content-Type: application/json' \
  -d '{"item_ids":["<ITEM_ID_1>","<ITEM_ID_2>"],"reason":"approved by ticket #123"}'
```

## 4. 错误码约定

- `400`：请求参数不合法
- `401`：API Key 缺失、错误或过期
- `403`：权限不足（缺少对应标签）
- `404`：资源不存在（job/action/key）
- `500`：服务异常

建议外部系统对 `401/403/500` 做告警，对 `400/404` 做输入校验回退。

## 5. 权限标签建议

- `job.read`
- `job.write`
- `command.execute`
- `command.approve`
- `policy.write`
- `audit.read`

可通过 `GET /v2/security/permission-templates` 获取模板。

## 6. Python SDK 示例（轻量）

```python
import time
import httpx

BASE = "http://127.0.0.1:8000"
KEY = "<API_KEY>"

payload = {
    "problem": "请定位多设备根因",
    "operation_mode": "diagnosis",
    "automation_level": "assisted",
    "devices": [
        {"host": "192.168.0.88", "protocol": "ssh", "username": "zhangwei", "password": "Huawei@123"},
        {"host": "192.168.0.102", "protocol": "ssh", "username": "zhangwei", "password": "Admin@123"},
    ],
}

with httpx.Client() as c:
    r = c.post(f"{BASE}/api/runs", headers={"X-API-Key": KEY}, json=payload)
    r.raise_for_status()
    run_id = r.json()["id"]

    while True:
        s = c.get(f"{BASE}/api/runs/{run_id}", headers={"X-API-Key": KEY}).json()
        if s["status"] in {"completed", "failed", "cancelled"}:
            break
        time.sleep(1)

    report = c.get(
        f"{BASE}/api/runs/{run_id}/report",
        headers={"X-API-Key": KEY},
        params={"format": "json"},
    ).json()
    print(report.get("summary"))
```

## 7. JavaScript SDK 示例（轻量）

```javascript
const BASE = 'http://127.0.0.1:8000'
const KEY = '<API_KEY>'

async function runJob() {
  const create = await fetch(`${BASE}/api/runs`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': KEY,
      'Idempotency-Key': `js-${Date.now()}`,
    },
    body: JSON.stringify({
      problem: '请定位多设备根因',
      operation_mode: 'diagnosis',
      automation_level: 'assisted',
      devices: [
        { host: '192.168.0.88', protocol: 'ssh', username: 'zhangwei', password: 'Huawei@123' },
        { host: '192.168.0.102', protocol: 'ssh', username: 'zhangwei', password: 'Admin@123' },
      ],
    }),
  })
  const created = await create.json()
  const runId = created.id

  while (true) {
    const resp = await fetch(`${BASE}/api/runs/${runId}`, { headers: { 'X-API-Key': KEY } })
    const run = await resp.json()
    if (['completed', 'failed', 'cancelled'].includes(run.status)) break
    await new Promise((r) => setTimeout(r, 1000))
  }

  const timelineResp = await fetch(`${BASE}/api/runs/${runId}/timeline`, { headers: { 'X-API-Key': KEY } })
  const timeline = await timelineResp.json()
  console.log(timeline.summary)
}

runJob().catch(console.error)
```

## 8. 生产接入建议

- 所有创建请求带 `Idempotency-Key`。
- `config` / 高风险执行场景必须引入审批链，不建议直连自动执行。
- 外部系统保存 `runId` 与 `Idempotency-Key` 的映射，避免重复运行。
- 对 `audit/report` 做定时归档。
- 尽量使用跳板机与最小权限设备账号。
