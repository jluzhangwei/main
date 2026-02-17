# HealthCheck 网络设备巡检工具（V2.1）

通过 SSH 批量登录网络设备执行巡检，支持 Web 执行、报告下载、AI 诊断（多模型）。

## 1. 功能总览

- 批量设备巡检：检查项（`@xxx`）+ 自定义命令（按行顺序）
- 多种接入：直连 / Jump SSH / SMC 命令接入
- 报告输出：JSON + CSV
- AI 诊断：ChatGPT / DeepSeek / Gemini / NVIDIA / 本地大模型（LM Studio）
- 分批分析：每台设备单独分析并汇总，支持进度显示
- 大报告分析模式：单设备按检查项分片分析，再做设备汇总与全局汇总
- 高精度模式：全量原文提交 AI（不裁剪）
- 角色权限：admin / user（admin 可管理模板与用户）
- 中英文界面、帮助文档双语

## 2. 目录结构

```txt
healthcheck/
├── healthcheck.py                 # CLI 启动入口（wrapper）
├── web_runner.py                 # Web 启动入口（wrapper）
├── app/
│   ├── healthcheck.py            # 巡检执行核心
│   ├── web_server.py             # Web 主服务（主入口）
│   ├── web_runner.py             # 兼容入口（转发到 web_server）
│   ├── llm_service.py            # 各模型 API 对接/连接测试
│   ├── analysis_pipeline.py      # AI 分析输入构造（分批/全量）
│   ├── prompt_service.py         # 提示词模板管理
│   └── state_store.py            # 本地状态存储
├── config/
│   └── command_map.yaml          # 检查项映射
├── check_templates/              # 检查项模板（默认/自定义）
├── prompts/
│   ├── system_default/
│   ├── system_custom/
│   ├── task_default/
│   └── task_custom/
├── output/reports/               # 巡检输出 JSON/CSV
├── runtime/tmp/                  # 临时文件
├── state/                        # 用户、会话、LLM配置、Token统计
├── scripts/
│   └── start_web.sh              # 推荐启动脚本（自动注入 OPENAI_CA_BUNDLE）
└── docs/
    └── readme.md
```

## 3. 启动方式（推荐）

```bash
cd healthcheck
./scripts/start_web.sh
```

默认地址：`http://127.0.0.1:8080`

兼容方式：

```bash
python3 web_runner.py
# 或
python3 app/web_server.py
```

## 4. 依赖与部署

- Python 3.9+（建议 3.10/3.11）
- 依赖：`paramiko`、`PyYAML`、`certifi`
- 目录写权限：`output/reports`、`runtime/tmp`、`state`、`prompts/*`
- 网络连通：设备 SSH（22）+ 所选云模型 API 域名

安装示例：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install paramiko PyYAML certifi
```

推荐用依赖文件一条命令安装：

```bash
python -m pip install -r requirements.txt
```

## 5. AI 分析模式说明

### 普通模式（默认）
- 为避免模型上下文超限，提交“关键证据 + 受控截断片段”
- 适合大批量设备和长输出场景

### 高精度模式
- 强制分批大小 = 1
- 提交全量原文 JSON（不裁剪）
- 精度高，但更慢、token 消耗更大，且更容易触发模型上下文上限

### 分批分析
- 每台设备单独提交给 AI
- 页面显示“设备进度 + 批次进度”
- 结束后生成逐设备结果和汇总分析

### 大报告分析模式
- 仅针对结构化 JSON 报告生效
- 单设备报告按“检查项分片”提交，降低上下文超限概率
- 分片结果先汇总为设备级结论，再进行全局汇总

## 6. 证书与连接测试（重要）

若 DeepSeek/NVIDIA/Gemini/OpenAI 连接测试报：
`CERTIFICATE_VERIFY_FAILED`

优先用推荐脚本启动（已自动设置）：

```bash
./scripts/start_web.sh
```

脚本会注入：
- `OPENAI_CA_BUNDLE=<certifi cacert.pem>`

说明：该设置同时影响 OpenAI/DeepSeek/Gemini/NVIDIA 的 HTTPS 校验。

## 7. 最近关键变更（V2.1）

- Web 主入口统一为 `app/web_server.py`，`web_runner.py` 保留兼容
- 新增 `scripts/start_web.sh`，解决云模型 SSL 证书链问题
- 新增 `requirements.txt`，支持新环境一条命令安装依赖
- AI 分析输入策略重构：
  - 默认模式启用受控限长（防上下文超限）
  - 高精度模式强制全量输入
- 分批分析路径与历史报告路径统一，进度显示更清晰
- 文档同步更新：目录结构、入口脚本、AI 模式与证书策略

## 8. 常见问题

1. 连接测试失败（证书错误）
- 使用 `./scripts/start_web.sh` 启动再测。

2. AI 分析只返回一台设备
- 开启分批分析可强制逐设备分析。
- 高精度全量模式可能因上下文超限导致失败，建议切回普通模式。

3. 分析很慢
- 关闭高精度，使用普通模式或分批大小 > 1。
- 如检查项超过 10 项，页面会提示：可能超模型窗口并降低分析速度。
