# NetOps AI V1

对话式网络设备故障排查平台（Web UI）。

## 功能

- 对话驱动故障诊断（连通性 / 接口 / 路由）
- 设备接入支持 SSH CLI / Telnet CLI / API 适配器
- 命令风险分级（低/中/高）
- 自动化等级开关：只读 / 半自动 / 全自动
- 高风险命令确认弹窗与审计时间线
- 会话导出（Markdown）

## 用户文档

- 图文用户说明书（中文）：[`docs/USER_GUIDE.zh-CN.md`](docs/USER_GUIDE.zh-CN.md)

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

- `POST /v1/sessions`
- `PATCH /v1/sessions/{id}`
- `POST /v1/sessions/{id}/messages` (SSE)
- `POST /v1/sessions/{id}/commands/{cmdId}/confirm`
- `GET /v1/sessions/{id}/timeline`
- `POST /v1/sessions/{id}/export`

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
