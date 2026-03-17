# NetDiag

命令驱动的网络设备故障诊断框架（FastAPI + Web GUI），以 `netlog_extractor` 为可用基线演进。

## 当前定位
- 保留 `netlog` 已验证能力：
  - 设备时间校准与时间窗换算
  - SMC 跳板登录与 Direct SSH
  - log 提取与过滤
  - 现有 Web UI 风格
  - AI 设置与提示词管理
- 新增 `netdiag` 诊断会话框架（脚手架阶段）：
  - 会话/轮次状态机
  - 命令计划与审批流程占位
  - `netdiag` API 路由骨架
  - AI Autopilot：基线自动识别 `vendor/os_family/model/version` 后，按 intent 自动映射诊断命令
  - Focus Lock：用户诊断方向持久化，逐轮覆盖率检查（covered/uncovered/ratio）
  - AI Learning：执行结果自动回写学习库，统计命令有效率趋势（overall/recent delta）
  - 官方命令库接入：支持 CSV/JSON 导入，planner 优先使用命令库规则，再回退内置 intent 映射
  - SOP 引擎：按“假设树 -> 证据评分 -> 停止条件”推进根因诊断
  - 证据解析器：按厂商+命令类型提取结构化信号（链路/路由/资源/时钟/防火墙）并回写 SOP 打分
  - 证据判定架构升级：解析器只做“事实抽取”（事实信号层），根因收敛由 AI + SOP 多证据一致性判定；不再依赖单厂商单场景硬编码收敛
  - 案例先验降权：Case Library 仅作为弱先验，避免覆盖设备直接证据
  - 已知问题库：支持现网问题案例导入，planning/analyze 自动检索并参与判定
  - 命中解释增强：已知问题返回 `match_reasons/matched_terms/matched_patterns/explain`，可直接追溯命中原因
  - 分析容错增强：LLM 超时时自动切换 deterministic analyzer fallback，仍给出可执行的证据链与下一步意图
  - 回合复盘：每轮产出 `retrospective` 与 `stop_decision`，用于持续优化
  - 设备状态库：支持状态点沉淀、历史基线对比（`[Historical Baseline Compare]`）
  - 配置历史库：支持快照采集与 diff 信号注入（`[Config Diff Review]`）
  - 案例库：支持从已完成会话沉淀案例，planning/analyze 按画像与域命中案例先验（低权重）
  - Lab Duel（对抗演进）：Red(模拟注入) vs Blue(诊断) vs Judge(评分) 闭环，支持自动晋升案例
  - 连接稳定性：LLM HTTPS 证书校验失败时自动尝试 certifi trust store（不降级到不校验）
  - 提示词已收敛为网络设备故障诊断：移除旧“网络日志/变更评审”默认模板，运行时自动迁移旧选择
  - 分析质量门禁：若模型输出缺少“当前判定/证据链/置信度/下一步命令/时间校验”或疑似截断，会自动修复为结构化诊断报告
  - 交互卡顿修复：`planning/analyzing` 仅在超时阈值后自动回收；`execute` 完成后会话状态回到可继续（不会假死在 running）
  - 分析默认时延优化：`analyze` 默认 `ai_timeout_sec=50`、`max_total_sec=50`、`ai_retries=1`，并收紧证据分片默认值（`max_chunks=4/chunk_size=1000`）
  - 分析硬时限：后端强制总预算 `<=50s`；外部信号（Zabbix/配置对比）会按预算降级或跳过，避免超过 1 分钟
  - 模型调用硬超时：LLM HTTP 请求超时改为由本轮预算驱动（含多端点 provider 的剩余预算切分），避免“名义超时已到但后台仍长时间等待”
  - 分析快速路径：当证据解析已出现直接证据或高收敛信号时，优先走 deterministic fast-path，避免每轮都等待大模型
  - 计划生成提速：`plan` 默认 `ai_timeout_sec=60`，并裁剪历史上下文长度，降低长会话下的卡顿
  - 模型可用性守卫：`intent/plan` 在首选模型不可用（如 ChatGPT key 缺失）时会快速切换到容灾模型；若都不可用则立即走 deterministic fallback，避免会话长期停留在 `AI 计划/planning`
  - 性能可观测：`plan/analyze` API 均返回 `performance`（阶段耗时），分析报告末尾追加 `[Performance]` 区块
  - 计划去基线化：Baseline 成功后，后续轮次默认抑制 `clock/version/cpu` 类“基线命令”重复进入计划（除非问题/方向明确要求时钟或资源复核）
  - 终端回显增强：右侧“设备终端回显”保留同一会话多轮命令记录（带 `R1/R2...` 标识），不会被新轮次覆盖
  - 命令执行去重：同会话同设备同命令仅执行一次；后续轮次自动复用已采集输出并标注 `reused@roundN`
  - 计划上下文增强：Planner 输入加入近轮次执行统计（ok/failed/reused），减少回到原点与重复采集
  - 对话可观测性增强：AI 对话中会明确输出“计划命令清单 + 执行明细（executed/reused）”
  - 对话区模型路由：用户输入框下支持“首选模型 + 容灾模型”菜单，作用于 `intent/plan/analyze`；主模型失败时自动切到容灾模型重试
  - 模型路由持久化与守卫：新会话沿用当前已选模型路由（不会回落到 ChatGPT 默认值）；切换模型/新会话时自动检查路由可用性并提示不可用或容灾切换状态
  - 刷新并发抑制：会话刷新改为单飞串行，避免并发刷新导致同状态卡片重复出现

## 自动化测试约束（强制）
后续自动测试与日志验收必须兼容 `netlog` 日志格式：
- transcript marker 兼容：`[LOGIN] [CMD] [OUT] [SEND] [RECV] [FLOW] [ERROR]`
- 不得随意改动现有日志语义，除非提供明确迁移方案。

## 目录
- `app/` 主应用
- `app/diagnosis/` NetDiag 会话框架（新）
- `docs/NETDIAG_ARCHITECTURE.md` 总体架构约束
- `docs/NETDIAG_AI_PROMPT_POLICY.md` 后续 AI 实施提示词工程（严格约束）
- `docs/NETCLAW_ARCHITECT_MASTER_PLAN.md` NetClaw 总体设计规划（架构师版）
- `docs/NETCLAW_ENGINEERING_EXECUTION_PLAN.md` NetClaw 工程实施计划（AI 工程师版）
- `docs/NETCLAW_ARCHITECT_ACCEPTANCE_CHECKLIST.md` NetClaw 架构验收清单（Architect Gate）
- `docs/NETDIAG_AI_AUTOPILOT.md` 自动识别+自动命令映射策略
- `docs/NETDIAG_CHECK_FLOW.md` Check 流程（循环状态机 + 手动 Next + 自动闭环）
- `docs/NETDIAG_INTERACTION_LOGIC_V2.md` 工程师式严格交互逻辑（后端状态机 + 前端动作卡）
- `docs/NETCLAW_AGENT_REQUIREMENTS.md` NetClaw Agent 模式需求文档（对话驱动、AI 主判、假设驱动循环）
- `docs/NETDIAG_UI_INTERACTION_STRATEGY.md` UI 简化与实时交互方略（按顺序执行）
- `docs/NETDIAG_LONGRUN_192168088.md` 真实设备 10 小时长跑测试方案与验收标准
- `docs/NETDIAG_REQUIREMENTS_V1.md` 可落地增强需求（状态库/配置库/案例库）
- `docs/NETDIAG_REQUIREMENTS_VISION.md` 想象版需求（中长期形态）
- `prompts/` 系统/任务提示词模板

## 当前落地状态（2026-03-12）
### 已完成
- 对话上下文合并：用户可分多次补充问题与时间信息
- 默认时间窗推进：未给明确时间窗时，AI 可先启动诊断
- 最小命令计划：首轮 `plan` 已压缩为最小探测命令集
- 本轮结论标准化：分析结果统一追加 `[Round Conclusion]`
- 直接证据收敛：命中明确根因后不再机械进入下一轮
- 模型路由回退：首选模型不可用时自动切容灾或 deterministic fallback
- 右侧分析显示区已接管聊天长详情：命令清单、执行明细、分析详情默认投送右侧
- 下一轮已开始继承上轮结论：会带着“目标验证提示”进入下一轮，而不是纯切轮
- 终端区已增强：跨轮次历史命令持续显示，且会话内尽量记住当前选中的命令
- 终端区已支持轮次过滤：`全部轮次 / 当前轮次 / 指定轮次`，全部视图下按轮次分组展示
- 下一轮目标验证提示已去重，不会重复堆积到 follow-up
- 终端区已支持快速检索：可按命令/设备/状态搜索
- 下一轮目标验证提示已压缩成更短的单步验证指令
- 终端区检索结果已支持高亮
- 主路径高频缺参提示已改成工程师式追问
- 下一轮目标验证提示已接入后续 `plan`，不再只是展示文本
- 下一轮目标已升级为结构化 `target_probe` 对象：会进入 `plan` API、存入 round、刷新后可恢复
- `target_probe` 现在可携带 `preferred_intents / expected_signals`，并直接影响下一轮选命令
- 下一枪目标已开始利用 `unmatched expected_signals` 反推 `preferred_intents`，优先补齐未验证证据
- `target_probe` 已补 `hypothesis_id / stop_if_matched` 字段，开始向统一验证任务对象收敛
- `target_probe` 已继续补 `expected_evidence / stop_reason / preferred_scope`，形成验证任务对象雏形
- `expected_signals` 已进入分析层：会输出命中/未命中/覆盖率，并写入 round 的 `evidence_overview`
- `expected_signals` 已开始影响收敛判定：全命中时可推动 `conclude_with_verification`，部分命中时可提升置信度
- 右侧分析显示区已固定显示 `target_probe / expected_signal_review / 当前判定`，不用再点折叠才能看到
- 右侧分析区与终端区已形成弱联动：当前验证目标会给相关命令打高亮
- 右侧分析区已支持“一键跳转到相关命令/结果”：可直接把终端焦点带到最相关的命令和回显
- 前端动作语义已开始脱离 `next_round`：用户侧统一显示为“继续验证”，顶部状态与历史列表也改为友好文案，不再直接暴露 `need_next_round`
- 后端 `session/next_action` 已补 `ui_status / ui_action` 元数据，前端后续重构可直接复用，不需要每个组件各自翻译动作/状态
- 下一枪目标已开始由后端分析阶段直接生成并持久化到 `evidence_overview.next_target_probe`，刷新/切页后不再只靠前端重新推断
- `session` / `next_action` 响应已直接携带 `next_target_probe`，前端恢复会话和“继续验证”动作优先消费服务端目标，不再负责主导拼装
- 统一验证任务对象已开始落地：分析阶段会生成 `validation_task`，把 `current_probe / next_probe / expected_signal_review / uncovered_goals / stop_reason` 收敛到单一结构里
- `validation_task` 已进入 `plan` 主链路：继续验证时前端会优先把统一任务对象发回后端，后端再从它提取 `target_probe` 驱动下一枪计划
- `validation_task` 已开始接管会话恢复与工作台渲染主源：后端会优先从 `validation_task.next_probe` 恢复下一枪目标，前端分析区/终端区/继续验证入口统一先读 `validation_task`，减少对 `target_probe / expected_signal_review` 的双轨依赖
- 后端 `next_action.action` 已切到 `continue_probe` 主语义，同时保留 `raw_action=next_round` 兼容层，动作状态机开始脱离“大轮次”命名
- `continue_probe` 已进入 `plan_round` 工作流许可链；同时 `session dump / next_action` 的 `target_probe + validation_task` 已统一走同一条 payload 生成链，减少恢复与动作卡出现两套下一枪目标的风险
- 终端区已开始按 `validation_task` 自动做验证焦点排序：未命中信号、优先意图、当前/下一枪目标命中的命令会优先排前，默认选中也会优先落到这些高价值命令上
- 前端分析区/终端区已进一步减少对 `round.target_probe / session.next_target_probe` 的直接读取，统一优先走 `_resolveValidationTask(...)`；当 `preferred_scope=related_commands` 且存在相关命令时，终端区会默认自动切到“相关视图”
- 服务端 `session dump` 现在会为 `rounds[*].evidence_overview` 注入标准化 `validation_task / next_target_probe`，前端恢复历史 round 时不再需要临时从旧字段拼装
- 前端 `_resolveValidationTask(...)` 已继续收紧为 `validation_task` 主源：当 round/session 已有标准化任务对象时，不再优先回读旧 `target_probe / next_target_probe / expected_signal_review`
- 顶部步骤条与“继续验证”同步逻辑已开始优先消费 `ui_status`，展示层继续摆脱对原始 `need_next_round` 的直接判断
- 终端区已新增验证任务聚类元数据：`相关视图` 下会优先按“未命中信号 / 优先意图 / 已命中信号 / 其他”分组与排序，当前验证目标对命令列表的收束更稳定
- `_buildNextTargetProbe(...)` 已继续收紧为 `validation_task` 驱动：在合成下一枪目标时不再优先直读旧 `next_target_probe / expected_signal_review`，而是优先基于统一任务对象推导
- 终端区 `相关视图` 已开始默认自动收束到最高价值簇：当验证任务要求 `related_commands` 且用户未搜索时，会优先只显示当前最关键的未命中/优先意图簇，减少噪声
- 前端已新增 `_sessionReadyForNextProbe(...)`，顶部状态、继续验证目标同步、按钮启用条件现在共用同一套“待继续验证”判断，不再分别猜测 `need_next_round / next_action / round.status`
- 终端区在自动收束场景下已开始固定“最优回放焦点”：相关视图自动收束时，不再优先恢复旧选中项，而是优先锁定当前最关键的运行中/待执行/高焦点命令
- 后端 `_session_continue_probe_payload(...)` 已去掉冗余 fallback，下一枪 payload 继续收向统一 `validation_task`
- 后端 `continue_probe` 已成为 canonical action：`next_action.raw_action` 现在直接返回 `continue_probe`，旧 `next_round` 仅作为 `legacy_action` 兼容字段保留
- `plan` 期 round 已直接持久化标准化 `validation_task / next_target_probe`，且 round 相关 API 响应统一走 `_round_response_payload(...)`，避免计划期与分析期返回结构断层
- 终端区已开始按验证任务自动选择最佳过滤范围：未手工锁定时，会在 `related/current/all` 之间自动落到最合适视图；用户手工切换后则按会话记住并尊重该选择
- 前端 `_resolveValidationTask(...)` 主路径已不再直接回读 `session.next_target_probe / next_action.target_probe`，统一优先消费标准化 `validation_task`
- 前端 `_resolveValidationTask(...)` 已进一步补齐标准化任务对象缺口：当已有 `validation_task` 但字段不完整时，会优先从 round 的 `target_probe / next_target_probe / focus_review / stop_decision` 补齐，而不是让后续 helper 再各自兜底
- 终端区在自动范围场景下已开始持续保持最佳焦点：未手工锁定且存在验证焦点时，会忽略旧选中项，优先固定到当前最佳命令，而不是被历史选择抢回
- 后端 `ui_status` 已开始按 workflow action 推导：当下一步是 `continue_probe` 时，即使 raw `status` 仍是旧值，前端也会稳定拿到 `ready_for_next_probe`
- 后端已开始在可继续验证的直接路径上真实写入 raw `ready_for_next_probe`：包括 Baseline 成功/复用、Resume、Execute 完成、Analyze 完成，以及部分 stale recovery，不再全部回写旧 `need_next_round`
- 终端区已取消额外 `expected_signal_review` 传递，统一从 `validation_task` 派生信号评审，继续减少前端双轨状态
- 项目内部测试与回归口径已开始切到 raw `ready_for_next_probe`，进一步减少把 `need_next_round` 当主状态的使用面
- 后端 `validation_task` 标准化已继续补强：当持久化任务对象字段不完整时，round 级标准化会优先用 `target_probe / next_target_probe / focus_review / stop_decision / expected_signal_review` 补齐，前端不再需要主链里重复补洞
- 前端 `_resolveValidationTask(...)` 已进一步收紧：当 round/session 已带标准化 `validation_task` 时直接使用该对象，旧 `target_probe / next_target_probe / expected_signal_review` 只留给历史兼容兜底
- 终端区自动范围切换已开始稳定保持最佳焦点：在自动范围和自动聚焦场景下，旧选中项不会再把焦点抢回，命令回放会持续落到当前最高价值命令
- 终端区自动范围已开始持续保持：未手工锁定过滤器时，系统会记住上一轮自动选出的最佳范围，后续刷新/轮次推进只要该范围仍有效就持续沿用，减少 `related/current/all` 之间来回抖动
- `plan_round` 已继续收向统一任务对象：继续验证时如果已有 `validation_task`，前端不再强制同时提交独立 `target_probe`，后端优先从统一任务对象提取下一枪目标
- `analyze_round` 与 `plan_round` 当前落盘的 `validation_task` 已统一经过上下文补齐，减少后续恢复/继续验证还需要再从旧字段二次拼装的概率
- 终端区自动聚类已开始持续保持：在 `related_commands` 自动收束场景下，系统会记住上一轮自动选中的最佳簇，只要该簇仍有效就持续沿用，减少相关命令簇在刷新和轮次推进中的来回跳动
- 边缘追问文案继续人话化：缺少时间窗时不再直接强调内部字段，而是引导用户给出大概时间，或由 AI 先按安全默认时间窗启动再收敛
- 终端区已支持 `相关视图` 过滤：可直接聚焦当前验证目标相关命令
- 未命中信号已开始进入后端计划链路：SOP 步骤与 PlannedCommand 会携带更具体的 `expected_signal`
- UI 已开始弱化“大轮次”心智：用户侧 `next_round` 展示改为“下一枪验证 / Next Probe”
- `stop_if_matched=true` 的验证任务已会压成单枪预算，进一步贴近“打一枪验证”的代理循环
- `expected_evidence / stop_reason` 已进入分析与收敛链路：命中后会按验证任务对象定义的动作收敛
- `preferred_scope=related_commands` 已开始驱动终端默认视图，相关验证任务会自动聚焦到“相关视图”
- `相关视图` 已开始按验证意图/期望证据分组，终端不再只是平铺相关命令
- `相关视图` 已可按“未命中信号 / 已命中信号 / 意图”分层，进一步贴近验证闭环

### 未完成
- 右侧工作台尚未彻底完成“上分析区、下终端区”重构
- 轮次循环尚未完全切成“下一枪验证式”的最小假设循环
- 证据层尚未统一成完整跨厂商结构化对象
- 工程师式追问仍有少量残余字段化文案
- `target_probe` 已进入主链路并补齐更多任务字段，但还没与完整结构化证据层完全打通
- `expected_signals` 已能做轻量评审，但还没进入统一证据判定与停止条件
- `expected_signals` 已开始进入停止条件，但仍未与完整结构化证据层统一建模
- 右侧分析显示区已能固定展示当前目标，但还没和终端区做深度联动高亮
- 终端区已能做相关命令高亮、一键跳转和相关视图过滤，但还没有更深的“按目标自动聚类”联动

### 上线前必须继续确认
- 历史命令回放在最终终端工作台中的呈现一致性
- 右侧显示区和对话动作卡是否还有重复展示
- 新分析路径是否都能稳定产出 `[Round Conclusion]`

## 环境
- Python 3.11+

## 安装
```bash
cd /Users/zhangwei/python/netdiag
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 运行
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```
访问：`http://127.0.0.1:8001`

## 主要页面
- `/netdiag` 诊断会话控制台（框架页）
  - 创建会话区已改为 3 步向导：`基础信息 -> 设备登录 -> 高级选项`
  - 流程操作区新增：`下一步建议` + `执行时间线` + 状态驱动按钮启停（防误操作）
  - 手动确认可用 `下一步` 单按钮推进；`下一轮诊断` 按钮用于切换并启动新一轮
  - 新增“左轮扳机视图”：每次 `下一步` 会射出当前步骤子弹，`下一轮诊断` 会满弹重装
- `/netdiag/control` 连接控制
  - 统一维护登录默认值（username/password/timezone/timeout）
  - 统一维护 SMC 跳板参数与 Zabbix 连接参数（SMC 跳板模式固定 smc）
- `/netdiag/sessions` 历史会话
  - 查看历史会话诊断过程（轮次/命令/分析摘要）
  - 支持一键“总结入案例库”（调用 `cases/from_session`）
  - 支持一键跳转回工作台并预填 `session_id`
- `/netdiag/learning` AI 进步看板 + 命令库管理 + 已知问题库管理
- `/netdiag/lab` Lab 对抗页（仅模拟注入，不执行设备配置）
- `/ai/settings` AI 设置（仅模型与提示词，不再放 SMC/Zabbix）

说明：旧 `task` 页面仍保留兼容访问，但主导航已精简为 netdiag 主流程。

## 主要 API（现阶段）
- `GET /api/tasks`
- `GET /api/tasks/{task_id}`
- `POST /api/tasks/{task_id}/analysis/start`
- `GET /api/analysis/{analysis_id}`
- `GET /api/netdiag/sessions`
- `POST /api/netdiag/sessions`
- `GET /api/netdiag/sessions/{session_id}`
- `GET /api/netdiag/sessions/{session_id}/next_action`
- `GET /api/netdiag/sessions/{session_id}/rounds/{round_no}/outputs`
  - 返回项包含 `reused/reused_from_round/reused_from_command_id`，用于识别复用采集
- `GET /api/netdiag/sessions/{session_id}/sop_state`
- `POST /api/netdiag/state/query`
- `GET /api/netdiag/state/{device_id}`
- `POST /api/netdiag/state/ingest`
- `POST /api/netdiag/config/snapshot`
- `GET /api/netdiag/config/{device_id}/history`
- `POST /api/netdiag/config/diff`
- `GET /api/netdiag/learning/summary`
- `GET /api/netdiag/learning/library`
- `POST /api/netdiag/learning/library/upsert`
- `POST /api/netdiag/learning/library/import_csv`
- `POST /api/netdiag/learning/library/import_json`
- `POST /api/netdiag/learning/library/{rule_id}/enabled`
- `DELETE /api/netdiag/learning/library/{rule_id}`
- `GET /api/netdiag/issues/library`
- `POST /api/netdiag/issues/library/upsert`
- `POST /api/netdiag/issues/library/import_csv`
- `POST /api/netdiag/issues/library/import_json`
- `POST /api/netdiag/issues/library/{issue_id}/enabled`
- `DELETE /api/netdiag/issues/library/{issue_id}`
- `POST /api/netdiag/issues/search`
- `GET /api/netdiag/cases/library`
- `POST /api/netdiag/cases/library/upsert`
- `POST /api/netdiag/cases/library/{case_id}/enabled`
- `DELETE /api/netdiag/cases/library/{case_id}`
- `POST /api/netdiag/cases/search`
- `POST /api/netdiag/cases/from_session/{session_id}`
- `GET /api/netdiag/lab/templates`
- `GET /api/netdiag/lab/duels`
- `GET /api/netdiag/lab/duels/{duel_id}`
- `POST /api/netdiag/lab/duels`
- `DELETE /api/netdiag/lab/duels/{duel_id}`
- `POST /api/netdiag/lab/duels/{duel_id}/inject`
- `POST /api/netdiag/lab/duels/{duel_id}/bind_blue_session`
- `POST /api/netdiag/lab/duels/{duel_id}/judge`
- `POST /api/netdiag/lab/duels/{duel_id}/rollback`
- `POST /api/netdiag/lab/duels/{duel_id}/promote_case`
- `GET /api/netdiag/zabbix/config`
- `POST /api/netdiag/zabbix/config`
- `POST /api/netdiag/zabbix/test`
- `POST /api/netdiag/zabbix/history`

## Lab Duel（新增）
用于加速案例库演进的受控对抗流程：
1. Red Agent 仅按模板生成“故障注入计划”（当前版本仅模拟，不下发设备配置）。
2. Blue Agent 使用现有 NetDiag 多轮诊断流程。
3. Judge 对 Blue 输出打分（域命中/根因词命中/证据信号命中/恢复验证）。
4. 达标结果可一键 `promote_case` 写入案例库。

强约束：
- 仅允许 `environment_tag=lab/test/sandbox`
- 仅允许 `mode=simulated`
- 当前版本不会在设备上执行注入命令

## 诊断 SOP（系统内置）
1. 生成候选根因（Hypotheses）：结合用户问题、Focus Goals、设备画像、已知问题命中。
2. 生成本轮验证命令（SOP steps）：按候选根因域（链路/路由/资源/防火墙/时钟）优先级下发 intent。
3. 执行并收集证据：仅 `show/display/dis`。
4. 证据回写评分：对候选根因加减分，更新置信度与状态（likely/possible/weak）。
   说明：评分同时使用“文本关键词 + 结构化证据信号 + 命令有效率”。
5. 停止条件判断：达到收敛阈值（或轮次上限）时建议收敛，否则给出下一轮聚焦方向。
6. 每轮输出复盘：执行成功率、头号假设分值变化、下一步建议。

## 一键自动诊断流程（UI）
在 `/netdiag` 的“流程操作”区域：
1. 勾选 `自动批准并执行（跳过人工确认）`。
2. 点击 `一键开始诊断`。
3. 系统将循环执行：`Create(如未提供 session_id) -> Baseline -> [Plan -> Approve -> Execute -> Analyze -> StopDecision]`。
4. 当 `StopDecision.recommend_conclude=true` 时自动执行 `Conclude`，否则自动进入下一轮继续诊断。

说明：
- 未勾选自动执行时，“一键开始诊断”按钮会置灰不可用。
- 运行状态与三块观测窗（思路/命令/输出）会随会话刷新。
- 自动流程无“人工轮次限制输入”；仅有内部安全保护（超时/异常停机保护）。

## 手动流程（单 Next）
在 `/netdiag` 的“流程操作”区域：
1. `round_no` 已改为只读“当前轮次”，不需要手工输入。
2. `下一步` 按钮会依据会话状态自动推进：`Baseline / Plan / Approve / Execute / Analyze / Conclude`。
3. 如需显式开启下一轮，可点 `下一轮诊断`（会自动切换轮次并发起新一轮 Plan）。

## 对话输入与经验库选择（新增）
1. 对话输入框使用 `Enter` 发送，`Shift+Enter` 换行；原“发送”按钮已取消。
2. 输入框右侧改为“经验库”弹出菜单（与自动批准同样交互样式），可按会话选择 AI 参考来源：
   - 已知问题库（Known Issues）
   - 案例库（Case Library）
   - SOP 库（SOP Library）
   - 命令经验库（Command Library）
3. 该菜单会作用到 `Plan` 与 `Analyze` 请求，后端按开关决定是否注入对应库信号/提示。
4. 当总开关关闭（Disabled）时，上述经验库全部不参与本轮计划与分析。

## 实时方略面板（新增）
右侧“实时执行观测”新增 `实时方略` 卡片，固定按 6 步显示当前进度：
1. 解析对话意图（设备/时间窗）
2. 连接配置守卫检查
3. 创建或切换会话
4. 采集 Baseline（仅一次）
5. 轮次循环（计划→批准→执行→分析）
6. 收敛判断并结束会话

说明：
- 每步状态仅三种：`待执行 / 下一步 / 完成`，用于降低理解成本。
- “下一步”在卡片头部实时显示（与 AI 对话动作卡保持一致）。
- 运行提示统一收敛到方略卡底部，不再分散在多个位置。
- 旧“左轮扳机/时间线”观测已下线，避免干扰主诊断流程。
- 聊天交互按会话持久化：刷新页面会保留当前会话与聊天内容；仅在“检测到新上下文并确认新建会话”时清空当前对话区。
- 急刹停止状态下，AI 动作卡同时提供“继续当前会话”和“新会话（清空对话）”两个入口。
- AI 对话标题栏右上角新增：`新建会话`（立即切到新草稿并清空当前对话）与 `历史会话`（打开历史会话页继续调用旧会话）。
- 对话回车后优先走 `AI 结构化解析`（接口：`POST /api/netdiag/intent/parse`），自动识别 `device_ip / fault_start / fault_end / focus_goals`；当 AI 不可用时自动回退本地规则解析。
- `新建会话` 现在会清空当前诊断上下文（问题/设备/时间窗/方向），避免右侧方略沿用旧会话输入。

## 官方命令库接入（推荐流程）
1. 进入 `/netdiag/learning` 页面。
2. 在 “Import Official Command Library” 粘贴 CSV（建议按厂商官方命令手册整理）。
3. 点击导入后，规则会持久化到 `state/netdiag_learning.json`。
4. 后续 planner 在同一 `intent + profile` 下会优先取命令库规则（按 `os_family/vendor + priority + score` 排序）。
5. 执行阶段会记录每条命令的结果信号（`valid_output / error_output / empty_output / failed`），并自动更新规则 `success/fail/score`。

CSV 表头示例：
```csv
vendor,os_family,intent,command,min_version,max_version,priority,enabled,source
huawei,huawei_vrp,interface_summary,display interface brief,,,200,true,official
cisco,cisco_iosxe,interface_summary,show ip interface brief,,,200,true,official
arista,arista_eos,system_log_recent,show logging,,,180,true,official
paloalto,paloalto_panos,pan_session_stats,show session info,,,220,true,official
```

## 现网已知问题库接入（推荐流程）
1. 进入 `/netdiag/learning` 页面中的 “Import Known Issue Library”。
2. 粘贴 CSV 后导入，数据持久化到 `state/netdiag_known_issues.json`。
3. planner/analyze 会自动按 `profile/version/query/evidence` 检索命中问题并注入提示。
4. 命中问题会参与候选根因排序，并影响 `stop_decision`。

CSV 表头示例：
```csv
issue_id,title,vendor,os_family,min_version,max_version,symptoms,evidence_patterns,diag_intents,diagnostic_commands,root_cause,fix_actions,verify_commands,severity,domain,priority,enabled,source
KI-001,BGP peer flap,cisco,cisco_iosxe,,,bgp flap;packet loss,neighbor.*idle,bgp_summary;system_log_recent,show ip bgp summary,Upstream peer instability,Check optics and peer side,show ip bgp summary,high,routing,150,true,noc
```

## Zabbix 历史数据回顾（新增）
1. 先在 `/netdiag/control` 里保存 Zabbix 连接信息（`base_url` + 鉴权）。
2. 在诊断流程或 API 中提供 `host + item_key + start_at + end_at` 拉取历史数据。
4. 时间窗较大（默认 >= 7 天）会自动走 `trend.get`，较短时间窗走 `history.get`。

`POST /api/netdiag/zabbix/history` 示例：
```json
{
  "host": "SG-IM1-SA-LAN-R053-Leaf-01",
  "item_key": "net.if.in[Eth1/49]",
  "start_at": "2026-03-08T12:00:00",
  "end_at": "2026-03-08T14:00:00",
  "timezone": "Asia/Singapore",
  "limit": 500
}
```

`POST /api/netdiag/sessions/{session_id}/rounds/{round_no}/analyze` 也支持可选附带：
```json
{
  "llm_route": {
    "primary": {"provider": "deepseek", "model": "deepseek-chat"},
    "failover": {"provider": "chatgpt", "model": "gpt-4.1-mini"}
  },
  "ai_timeout_sec": 50,
  "ai_retries": 1,
  "max_total_sec": 50,
  "external_signal_timeout_sec": 6,
  "zabbix_history": {
    "host": "SG-IM1-SA-LAN-R053-Leaf-01",
    "item_key": "system.cpu.util",
    "start_at": "2026-03-08T12:00:00",
    "end_at": "2026-03-08T14:00:00",
    "timezone": "Asia/Singapore"
  }
}
```
附带后，Zabbix 结果会被解析为结构化信号并参与 SOP 评分。
说明：前端单次动作超时窗口为 480 秒，若步骤被中断，可直接在 AI 动作卡继续下一步，不需要重建会话。

## AI 对话驱动诊断逻辑（当前落地）
1. 用户只需在 AI 对话输入：现象 + 设备 IP + 时间描述（支持相对时间如“昨天到今天”）。
2. 前端优先走 AI 意图解析，失败自动回退本地规则；全角 IP（如 `192。168。0。88`）会自动归一。
3. 会话未创建时由“下一步”自动触发创建；开启自动批准策略后可自动推进创建。
4. Baseline 每会话仅采集一次；后续轮次只做 `计划 -> 批准 -> 执行 -> 分析 -> 下一轮/收敛`。
5. 已执行命令按 `device_id + command` 跨轮复用，避免重复下发同命令。
6. 右侧窗口拆分为：
   - 上半：分析显示区（AI 推理、计划、证据折叠详情、结果 JSON 实时同步）。
   - 下半：设备终端回显（命令输出尾部实时刷新）。
7. AI 对话区保持简洁：命令列表/执行明细用折叠卡片，详情可一键切到右侧显示区查看。
8. 详细流程图式说明：见 `docs/NETDIAG_DIALOG_FLOW.md`。
9. 自动批准续跑防卡顿：前端已将 `GET 刷新` 与“可中断动作”控制器解耦，避免出现“提示自动继续但未真正推进下一步”。
10. 直接证据优先收敛：当命中 `admin down / shutdown` 等强证据时，会提升链路域假设并优先给出收敛结论，减少无意义下一轮。
11. 快速计划默认覆盖多轮：在无 follow-up 时，默认走 fast deterministic planner（可通过 `fast_plan_first_round_only` 改回仅首轮）。

## 测试
```bash
PYTHONPATH=. .venv/bin/pytest -q
```

## 自动性能回归 Skill（推荐）
- Skill 目录：`~/.codex/skills/netdiag-auto-perf-test`
- 主脚本：`~/.codex/skills/netdiag-auto-perf-test/scripts/run_netdiag_perf_test.py`
- 用途：自动执行 `create -> baseline -> plan -> approve -> execute -> analyze`，并输出每一步耗时、慢点归因、优化尝试。
- 默认目标设备可直接用 `192.168.0.88`，支持多轮连续压测。

示例：
```bash
python3 ~/.codex/skills/netdiag-auto-perf-test/scripts/run_netdiag_perf_test.py \
  --base-url http://127.0.0.1:8001 \
  --device-ip 192.168.0.88 \
  --username "$NETDIAG_USERNAME" \
  --password "$NETDIAG_PASSWORD" \
  --rounds 2 \
  --auto-retry-analyze 1 \
  --auto-conclude \
  --output-dir /Users/zhangwei/python/netdiag_perf_reports
```

输出：
- `netdiag_perf_*.json`：结构化耗时/瓶颈/错误数据
- `netdiag_perf_*.md`：可读报告（瓶颈排行与优化动作）

## 真实设备 10h 长跑回归
- 长跑脚本：`scripts/netdiag_longrun_test.py`
- 典型启动命令：
```bash
cd /Users/zhangwei/python/netdiag
./.venv/bin/python scripts/netdiag_longrun_test.py \
  --hours 10 \
  --interval-sec 120 \
  --device-ip 192.168.0.88 \
  --username zhangwei \
  --password '***' \
  --fault-start 2026-03-10T20:30:00 \
  --fault-end 2026-03-10T23:59:59 \
  --timezone Asia/Singapore \
  --output-dir /Users/zhangwei/python/netdiag/output/longrun/longrun_192168088_20260310
```
- 产物：
  - `runtime.log`：运行状态
  - `iterations.jsonl`：每轮诊断结果（含是否命中 shutdown）
  - `summary.json`：累计通过率与失败摘要
- 参数说明：`--password` 为必填，不在脚本中提供默认值。
- 详细测试流程与验收：见 `docs/NETDIAG_LONGRUN_192168088.md`

## 2h 连续回归（当前建议）
```bash
cd /Users/zhangwei/python/netdiag
./.venv/bin/python scripts/netdiag_longrun_test.py \
  --hours 2 \
  --interval-sec 30 \
  --base-url http://127.0.0.1:8001 \
  --device-ip 192.168.0.88 \
  --username zhangwei \
  --password '***' \
  --fault-start 2026-03-10T20:30:00 \
  --fault-end 2026-03-10T23:59:59 \
  --timezone Asia/Singapore \
  --output-dir /Users/zhangwei/python/netdiag/output/longrun/engineer_mode_2h_<ts>
```

运行中可观察：
- `runtime.log`：每轮耗时与 pass/fail。
- `iterations.jsonl`：每轮结构化结果（含 shutdown 证据命中）。
- `summary.json`：累计 pass_rate，适合页面/脚本轮询展示。

## 问题清单回归（192.168.0.88）
- 清单文档：`docs/NETCLAW_ISSUE_LANDING_CHECKLIST_192168088.md`
- 回归脚本：`scripts/netdiag_issue_regression.py`
- 用途：把用户反复提出的问题转成可执行验收项，并在真实设备上做一次“路由守卫 / 自然语言解析 / baseline 仅一次 / 命令去重 / analyze 收敛 / stop-resume-history”回归。

```bash
cd /Users/zhangwei/python/netdiag
/Users/zhangwei/python/.venv/bin/python scripts/netdiag_issue_regression.py \
  --base-url http://127.0.0.1:8001 \
  --device-ip 192.168.0.88 \
  --username zhangwei \
  --password '***' \
  --iterations 3
```

输出：
- `issue_regression_report.json`：每个问题项的 PASS/FAIL 与证据

## 注意
本工程当前是“可运行基线 + 会话框架脚手架”阶段。
下一阶段在不破坏 netlog 兼容能力前提下，逐步落地命令审批、证据解析和多轮 AI 判定。
- `plan_round` 已进一步收向统一任务对象：后端现在优先从 `validation_task` 提取 `target_probe`，独立 `target_probe` 仅作为兼容输入兜底
- 终端区已开始持续记住自动选中的最佳相关命令簇：当自动收束场景仍成立且该簇还有效时，刷新和轮次推进后会继续沿用该簇，减少相关命令分组跳动
- 终端区已新增自动回放焦点持久化：自动聚焦场景下会记住上一轮自动选中的最佳命令，只要该命令仍有效就继续沿用，避免刷新和继续验证后跳回旧手工选中项
- 边缘追问文案继续工程师化：时间窗不明确时，会说明 AI 将先按安全默认时间窗启动、先看设备证据、再继续缩小范围，不再暴露内部字段式表达
- 前端 `_resolveValidationTask(...)` 已进一步收口为仅消费标准化 `validation_task`：主链不再从 `target_probe / next_target_probe / expected_signal_review` 现拼任务对象，旧字段只保留后端兼容镜像
- 后端 helper 主链已开始优先消费 `validation_task`：`期望信号评审 / 收敛判定修正 / 下一枪目标推导` 这三条链不再先依赖旧 `target_probe / expected_signal_review`，而是优先从统一任务对象取值
- 前端生成的下一枪目标 `source` 已统一改为 `continue_probe`，继续验证请求只在确实没有 `validation_task` 时才附带兼容 `target_probe` 字段，进一步削减旧 `next_round` 语义残留
- 前端 UI 配置表已进一步去掉对 `next_round / need_next_round` 的显式依赖：动作卡、自动推进、步骤条现在统一按 `continue_probe / ready_for_next_probe` 工作，旧别名只保留在归一化兼容入口
- 历史会话状态兼容已继续收口到真实状态流：`session_manager` 在载入持久化会话和调用 `set_status()` 时，会把旧 `need_next_round` 统一归一成 raw `ready_for_next_probe`，旧主状态不再重新进入内存主链
- `need_next_round` 已从工程主状态集合中移除：项目内部主状态现在统一为 raw `ready_for_next_probe`，旧状态只保留在加载归一化和兼容映射中
- 前端 `_buildNextTargetProbe(...)` 已继续收口为仅消费标准化 `validation_task`：不再从旧 round 字段本地二次推断下一枪目标
- 前端继续验证提交流已完全改为只提交 `validation_task`：正常主链不再主动发送兼容 `target_probe`，旧字段仅由后端兼容入口保留
