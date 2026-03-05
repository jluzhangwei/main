# HealthCheck 网络设备巡检工具（V2.4）

通过 SSH 批量登录网络设备执行巡检，支持 Web 执行、报告下载、AI 诊断（多模型）。

## 1. 功能总览

- 批量设备巡检：检查项（`@xxx`）+ 自定义命令（按行顺序）
- 多种接入：直连 / Jump SSH / SMC 命令接入
- 报告输出：JSON + CSV
- AI 诊断：ChatGPT / DeepSeek / Gemini / NVIDIA / 本地大模型（LM Studio）
- 分批分析：每台设备单独分析并汇总，支持进度显示
- 分片模式：单设备按检查项分片分析，再做设备汇总与全局汇总
- AI 结果保存：支持“保存分析报告”为 Markdown（`.md`）
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
│   ├── llm_adapter.py            # LLM 调用适配层（统一 run/probe/model_used）
│   ├── analysis_pipeline.py      # AI 分析输入构造（分批/全量）
│   ├── analysis_guard.py         # AI 分析预估与结果覆盖校验
│   ├── analysis_service.py       # AI 分析编排服务（分批/分片/汇总）
│   ├── status_service.py         # 分析状态存储与查询服务
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
├── run.sh                        # 推荐统一启动入口（自动建虚拟环境、安装依赖、清理端口、支持热重载）
├── scripts/
│   ├── start_web.sh              # Web 启动脚本（自动注入 OPENAI_CA_BUNDLE）
│   └── dev_reload.py             # 开发热重载
└── docs/
    └── readme.md
```

## 3. 启动方式（推荐）

```bash
cd healthcheck
./run.sh
```

默认地址：`http://127.0.0.1:8080`

说明：
- `run.sh` 会自动选择 Python 版本并创建/复用虚拟环境
- 自动安装 `requirements.txt`
- 启动前自动释放占用端口（默认 `8080`）
- 默认开启热重载（可用 `--no-reload` 关闭）

兼容方式：

```bash
./scripts/start_web.sh
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

### 分批分析
- 每台设备单独提交给 AI
- 页面显示“设备进度 + 批次进度”
- 结束后生成逐设备结果和汇总分析
- 支持参数调节：`AI 并发数（设备级）`、`每设备失败重试`

### 分片模式
- 仅针对结构化 JSON 报告生效
- 单设备报告按“检查项分片”提交，降低上下文超限概率
- 分片结果先汇总为设备级结论，再进行全局汇总

### 分析预估（P0）
- 支持“分析预估”按钮：执行前给出设备数、预计调用次数、预计 Token、预计耗时
- 当预计 Token 过高时，执行前会弹窗二次确认
- 便于在大报告场景提前调整并发/分片参数，降低失败率

### 汇总覆盖校验（P0）
- 分批/分片汇总后，程序会校验“全设备逐台覆盖”
- 若模型汇总遗漏设备，程序会自动补齐缺失设备行（标记“待复核”）
- 防止最终汇总静默漏设备

### 分析结果另存为（.md）
- AI 分析按钮旁提供“保存分析报告”
- 优先调用浏览器原生保存对话框（可选路径+文件名）
- 浏览器不支持时回退为下载方式，默认扩展名 `.md`
- 保存内容包含：生成时间、模型来源、任务 ID 与分析正文

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

## 7. 最近关键变更（V2.4）

- 顶部导航与页面头部风格统一对齐 Netlog Extractor（Create Task / Tasks / AI Settings / Admin）
- AI 设置区按钮命名简化为：`Import API Key`、`Test Connection`、`Save Config`
- 文档页与主页面导航样式统一，语言切换逻辑联动
- 新增统一启动入口 `run.sh`（自动端口清理 + 默认热重载）

## 8. 最近关键变更（V2.3）

- AI 分析区新增“保存分析报告”按钮，支持另存为 `.md`
- AI 服务层拆分完成：
  - `analysis_service.py`（分析编排）
  - `status_service.py`（状态存取）
  - `llm_adapter.py`（模型适配）
- `web_server.py` 聚焦为请求编排层，减少超大文件耦合

## 9. 最近关键变更（V2.2）

- 新增 `analysis_guard.py`，提供分析前规模预估与汇总覆盖校验
- 新增前端“分析预估”按钮，展示预计调用次数/Token/耗时并给出风险提示
- 新增 `POST /analysis_precheck` 接口，支持本次报告与历史 JSON 报告预估
- 分批结果缺失时自动补占位，避免设备被静默遗漏

## 10. 最近关键变更（V2.1）

- Web 主入口统一为 `app/web_server.py`，`web_runner.py` 保留兼容
- 新增 `scripts/start_web.sh`，解决云模型 SSL 证书链问题
- 新增 `requirements.txt`，支持新环境一条命令安装依赖
- AI 分析输入策略重构：默认模式启用受控限长（防上下文超限）
- 分批分析路径与历史报告路径统一，进度显示更清晰
- 文档同步更新：目录结构、入口脚本、AI 模式与证书策略

## 11. 常见问题

1. 连接测试失败（证书错误）
- 使用 `./scripts/start_web.sh` 启动再测。

2. AI 分析只返回一台设备
- 开启分批分析可强制逐设备分析。
- 若报告过大，建议启用分片模式降低上下文超限风险。

3. 分析很慢
- 使用普通模式或分批并发适当调低。
- 如检查项超过 10 项，页面会提示：可能超模型窗口并降低分析速度。
