# NetLog Extractor

网络设备 buffer log 提取工具（FastAPI + Web GUI）。

## 功能
- 单台设备与批量设备（文本/CSV）任务
- Direct SSH（`asyncssh`）
- SMC 跳板模式（`pty + subprocess`）
- 后台并发任务 + 超时控制
- 基于设备时钟计算 offset：`device_time - reference_time(服务器本机时间)`
- 时间段过滤 + 上下文行合并去重
- 下载：单设备 `filtered.log`、任务 `summary.json`、任务 `download.zip`
- Debug 模式：记录登录/命令/返回到每台设备 `debug.log`，并可在任务详情页实时查看
- AI 分析：继承 healthcheck 风格的多提供商设置（chatgpt/local/deepseek/gemini/nvidia）与系统/任务提示词模板，对任务日志做诊断分析

## 环境
- Python 3.11+

## 安装
```bash
cd netlog_extractor
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 运行
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
访问：`http://127.0.0.1:8000`

或使用一键脚本：
```bash
chmod +x run.sh test.sh
./run.sh
```

## 页面说明
- `/` 创建任务
- `/tasks` 任务列表
- `/tasks/{task_id}` 任务详情（自动轮询 `/api/tasks/{task_id}`）
- `/ai/settings` AI 提供商与提示词设置

## CSV 字段
支持以下列名：
- `device_ip` (必填)
- `device_port` (可选, 默认22)
- `device_name` (可选)
- `username` (可选, 默认全局)
- `password` (可选, 默认全局)
- `vendor_hint` (可选: cisco/arista/huawei)
- `jump_mode` (可选: direct/smc, 默认全局)
- `jump_host`, `jump_port`, `smc_command` (smc时可覆盖全局)

示例：
```csv
device_ip,device_port,device_name,username,password,vendor_hint,jump_mode,jump_host,jump_port,smc_command
10.0.0.1,22,core-sw,admin,pass123,cisco,direct,,,
10.0.0.2,22,edge-hw,netops,pass456,huawei,smc,1.2.3.4,22,smc server toc 1.2.3.4
```

## 批量文本输入格式
每行：`ip[:port],device_name,vendor_hint`

示例：
```text
10.0.0.1:22,core-1,cisco
10.0.0.2,agg-1,huawei
```
批量文本模式默认使用全局 username/password。

## 输出目录
- 任务目录：`./output/{task_id}`
- 每台设备：
  - `raw.log`
  - `filtered.log`
  - `meta.json`
- 任务级：`summary.json`

## API
- `GET /api/tasks`
- `GET /api/tasks/{task_id}`
- `GET /api/tasks/{task_id}/summary.json`
- `GET /api/tasks/{task_id}/download.zip`
- `GET /api/tasks/{task_id}/devices/{device_id}/log`
- `GET /api/tasks/{task_id}/devices/{device_id}/debug`
- `POST /api/tasks/{task_id}/analysis/start`
- `GET /api/analysis/{analysis_id}`

## 测试
```bash
pytest -q
```

或：
```bash
./test.sh
```

## 常见问题
`CERTIFICATE_VERIFY_FAILED`（模型连接测试时报 SSL 证书错误）：

1. 推荐使用 `run.sh` 启动（会自动注入 `OPENAI_CA_BUNDLE`）。
2. 或手动指定证书链：
```bash
export OPENAI_CA_BUNDLE="$(python -c 'import certifi; print(certifi.where())')"
./run.sh
```
3. 临时排障（不推荐生产）可关闭校验：
```bash
export OPENAI_SSL_NO_VERIFY=1
./run.sh
```
