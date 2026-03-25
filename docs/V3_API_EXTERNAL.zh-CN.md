# V3 对外 API 接入说明（/v2）

本文档用于外部系统将 NetOps AI 作为能力服务接入。

## 1. 鉴权

- 头部：`X-API-Key: <KEY>`
- 首次引导（系统尚无 key）：可直接调用 `POST /v2/keys` 创建管理员 key。

## 2. 核心流程

1. 创建任务：`POST /v2/jobs`
2. 轮询状态：`GET /v2/jobs/{jobId}` 或 `GET /v2/jobs/query`
3. 订阅事件：`GET /v2/jobs/{jobId}/events?from_seq=0`（SSE）
4. 审批命令组（repair 模式）：
   - 单组：`/approve`、`/reject`
   - 批量：`/approve-batch`、`/reject-batch`
5. 拉取结果：`GET /v2/jobs/{jobId}/timeline` / `GET /v2/jobs/{jobId}/report`

## 3. 请求示例

### 3.1 创建任务

```bash
curl -sS -X POST 'http://127.0.0.1:8000/v2/jobs' \
  -H 'X-API-Key: <KEY>' \
  -H 'Idempotency-Key: ext-job-001' \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "external-call-demo",
    "problem": "多设备异常关联分析",
    "mode": "diagnosis",
    "topology_mode": "hybrid",
    "max_gap_seconds": 300,
    "max_device_concurrency": 20,
    "devices": [
      {"host":"192.168.0.88","protocol":"ssh","username":"zhangwei","password":"Huawei@123"},
      {"host":"192.168.0.102","protocol":"ssh","username":"zhangwei","password":"Admin@123"}
    ]
  }'
```

### 3.2 批量审批

```bash
curl -sS -X POST 'http://127.0.0.1:8000/v2/jobs/<JOB_ID>/actions/approve-batch' \
  -H 'X-API-Key: <KEY>' \
  -H 'Content-Type: application/json' \
  -d '{"action_group_ids":["<GROUP_ID_1>","<GROUP_ID_2>"],"reason":"approved by ticket #123"}'
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
    "mode": "diagnosis",
    "devices": [
        {"host": "192.168.0.88", "protocol": "ssh", "username": "zhangwei", "password": "Huawei@123"},
        {"host": "192.168.0.102", "protocol": "ssh", "username": "zhangwei", "password": "Admin@123"},
    ],
}

with httpx.Client() as c:
    r = c.post(f"{BASE}/v2/jobs", headers={"X-API-Key": KEY}, json=payload)
    r.raise_for_status()
    job_id = r.json()["id"]

    while True:
        s = c.get(f"{BASE}/v2/jobs/{job_id}", headers={"X-API-Key": KEY}).json()
        if s["status"] in {"completed", "failed", "cancelled"}:
            break
        time.sleep(1)

    report = c.get(
        f"{BASE}/v2/jobs/{job_id}/report",
        headers={"X-API-Key": KEY},
        params={"format": "json"},
    ).json()
    print(report["job"]["rca_result"])
```

## 7. JavaScript SDK 示例（轻量）

```javascript
const BASE = 'http://127.0.0.1:8000'
const KEY = '<API_KEY>'

async function runJob() {
  const create = await fetch(`${BASE}/v2/jobs`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': KEY,
      'Idempotency-Key': `js-${Date.now()}`,
    },
    body: JSON.stringify({
      problem: '请定位多设备根因',
      mode: 'diagnosis',
      devices: [
        { host: '192.168.0.88', protocol: 'ssh', username: 'zhangwei', password: 'Huawei@123' },
        { host: '192.168.0.102', protocol: 'ssh', username: 'zhangwei', password: 'Admin@123' },
      ],
    }),
  })
  const created = await create.json()
  const jobId = created.id

  while (true) {
    const resp = await fetch(`${BASE}/v2/jobs/${jobId}`, { headers: { 'X-API-Key': KEY } })
    const job = await resp.json()
    if (['completed', 'failed', 'cancelled'].includes(job.status)) break
    await new Promise((r) => setTimeout(r, 1000))
  }

  const timelineResp = await fetch(`${BASE}/v2/jobs/${jobId}/timeline`, { headers: { 'X-API-Key': KEY } })
  const timeline = await timelineResp.json()
  console.log(timeline.job.rca_result)
}

runJob().catch(console.error)
```

## 8. 生产接入建议

- 所有创建请求带 `Idempotency-Key`。
- `repair` 模式必须引入审批链，不建议直连自动执行。
- 外部系统保存 `jobId` 与 `Idempotency-Key` 的映射，避免重复任务。
- 对 `audit/report` 做定时归档。
- 尽量使用跳板机与最小权限设备账号。
