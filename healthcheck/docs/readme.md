# HealthCheck 网络设备巡检工具

通过 SSH 批量登录网络设备执行巡检，支持：
- 勾选意图检查项（`@xxx`）
- 自定义命令按顺序执行
- Web 页面执行与任务状态追踪
- JSON/CSV 报告下载
- ChatGPT / DeepSeek / 本地大模型（LM Studio）分析报告

## 目录结构

```txt
healthcheck/
├── healthcheck.py                # CLI 启动入口（wrapper）
├── web_runner.py                # Web 启动入口（wrapper）
├── app/
│   ├── healthcheck.py           # 核心巡检执行逻辑
│   └── web_runner.py            # Web 服务与页面逻辑
├── config/
│   └── command_map.yaml         # 意图到命令映射 + 设备画像规则
├── data/
│   ├── devices.txt              # 设备示例
│   └── intents.txt              # 检查项示例
├── docs/
│   └── readme.md
├── output/
│   └── reports/                 # 巡检报告输出目录（JSON/CSV）
├── runtime/
│   └── tmp/                     # 运行时临时文件
├── prompts/
│   ├── system_default/          # 内置系统提示词模板（严格约束）
│   ├── system_custom/           # 自定义系统提示词模板
│   ├── task_default/            # 内置任务提示词模板（诊断目标）
│   └── task_custom/             # 自定义任务提示词模板
└── state/
    └── gpt_config.json          # LLM 配置（本地保存）
```

## 路径规则

- 已统一使用相对项目根目录路径，不依赖绝对路径。
- 默认命令映射文件：`config/command_map.yaml`
- 默认报告目录：`output/reports/`
- 默认意图文件：`data/intents.txt`
- LLM 配置文件：`state/gpt_config.json`
- 提示词模板目录：`prompts/system_default/`、`prompts/system_custom/`、`prompts/task_default/`、`prompts/task_custom/`

## 快速运行

### CLI 模式

```bash
cd healthcheck
python3 healthcheck.py
```

程序默认提示：
- Command map 默认值：`config/command_map.yaml`
- 设备地址可直接输入，或输入相对路径文件（如 `data/devices.txt`）
- 命令/意图可直接输入，或输入相对路径文件（如 `data/intents.txt`）

### Web 模式

```bash
cd healthcheck
python3 web_runner.py
```

浏览器访问：`http://127.0.0.1:8080`

## Web 功能说明

- 首页：输入账号、设备地址、检查项、自定义命令、执行参数（并发/重试/debug）
- 设备导入：导入后直接刷新到“设备地址”文本框，可继续手动编辑
- 检查项：按分类展示，分类支持全选
- 任务状态页：实时日志、状态、仅展示本次报告下载
- 报告分析：支持分析本次任务或上传任意历史报告文件

## LLM 分析配置

支持三种模型来源：
- ChatGPT（OpenAI）
- DeepSeek
- 本地大模型（LM Studio）

说明：
- API Key 支持导入保存，后续可复用；再次保存会覆盖旧值并提示
- 默认 LM Studio：`http://192.168.0.99:1234`
- 默认本地模型：`qwen/qwen3-coder-30b`
- 诊断提示词支持“双层模式”：
  - 系统提示词（固定约束，建议使用“网络工程师-严格模式”）
  - 任务提示词（本次诊断目标，可选模板或空模板）

## 报告字段说明

- `attempt_output_preview`：压缩预览内容（便于快速浏览）
- `attempt_output_full`：完整原始命令回显（用于精确分析）

## 常见问题

1. 只跑了一台设备
- 先检查设备地址输入是否被分隔（换行/逗号/分号）；导入文件后请确认文本框内容。

2. 页面转圈但无输出
- 可开启 debug 模式查看完整日志；并确认设备 SSH 可达、账号密码正确。

3. OpenAI 证书错误（CERTIFICATE_VERIFY_FAILED）
- 属于本机/网络证书链问题。可先改用本地大模型，或修复系统证书链。
