# NetClaw 架构验收清单（Architect Gate）

## 1. 验收原则
架构师只以“可证明行为”验收，不以“主观感觉”“代码存在”“单点截图”验收。

## 2. 必过门禁
### G1 对话驱动
- 用户可分多次提供信息
- AI 能合并上下文，不要求重复输入完整表单

### G2 默认推进
- 用户未给明确时间窗时，AI 能默认启动
- 不能因为缺 `fault_start/fault_end` 卡死

### G3 最小验证
- 每轮命令数量应尽量小
- 命令必须服务于当前假设

### G4 不重复执行
- 同会话同设备同命令仅执行一次
- 后续轮次必须复用历史输出

### G5 本轮结论
- 每轮结束必须明确：
  - 当前判定
  - 证据链
  - 下一步原因
  - 是否已收敛

### G6 直接证据收敛
- 命中明确根因时，不允许继续机械进入下一轮

### G7 模型回退
- 首选模型不可用时，不允许长期卡在 `planning/analyzing`
- 必须自动切容灾或 deterministic fallback

### G8 UI 一致性
- 动作按钮只在对话内按需出现
- 右侧分析区与终端区职责清晰
- 停止按钮只在输入区右侧

## 3. 验收证据类型
至少满足以下之一：
1. 自动化测试
2. API 结果
3. 真实设备回归
4. 明确的性能数据

## 4. 不通过条件
任一出现即判不通过：
1. 仍要求用户手填关键字段才能开始
2. AI 只输出状态，不输出结论
3. 相同命令重复执行
4. 已有直接证据仍继续下一轮
5. 模型失败时长期卡住

## 5. 架构师最终输出格式
### 5.1 实际完成了
- 只列已被证据证明的项

### 5.2 还没有完成
- 明确指出未闭环项

### 5.3 风险
- 指出可能回归的点

### 5.4 下一阶段
- 只列最关键的 1-3 项

## 6. 当前架构验收状态（2026-03-12）
### 6.1 实际完成了
- `G1 对话驱动`：已支持按整段对话上下文理解，用户可分多次补充问题/时间信息。
  - 证据：`tests/test_intent_parse_endpoint.py`
- `G2 默认推进`：未给明确时间窗时，会话可按默认启动时间窗继续推进，不再被 `fault_start/fault_end` 硬阻塞。
  - 证据：`tests/test_session_create_agent_mode.py`
- `G3 最小验证`：计划器已接入 `minimal_probe_budget`，首轮默认压缩为最小探测命令集。
  - 证据：`app/routers/netdiag.py` 中 `_minimal_probe_budget()`；`tests/test_plan_fast_deterministic.py`
- `G5 本轮结论`：`analyze` 已统一追加标准化 `[Round Conclusion]` 结论块，前端可稳定提取。
  - 证据：`app/routers/netdiag.py` 中 `_append_round_conclusion()`；`tests/test_analyze_fastpath.py`
- `G6 直接证据收敛`：命中明确直接证据后，不再继续机械进入下一轮。
  - 证据：`docs/NETCLAW_ISSUE_LANDING_CHECKLIST_192168088.md`；真实设备 `192.168.0.88` 回归
- `G7 模型回退`：首选模型不可用时会自动切容灾或 deterministic fallback，不再长期卡在 `planning/analyzing`。
  - 证据：`tests/test_llm_route_selector.py`、`tests/test_plan_fast_deterministic.py`
- `G8 UI 一致性`：已完成第一阶段收敛，聊天中的命令/分析详情会自动投送到右侧显示区，不再默认在聊天区展开长详情。
  - 证据：`app/templates/netdiag_home.html`；`GET /netdiag` 冒烟通过
- 终端区历史回放：已支持跨轮次历史命令持续显示，刷新后同会话内尽量保持命令选中项。
  - 证据：`tests/test_round_outputs_endpoint.py`
- 终端区可控性：已支持按轮次过滤与全部轮次分组展示，便于回看历史执行链。
  - 证据：`GET /netdiag` 冒烟通过；前端工作台渲染逻辑已落地
- 终端区检索：已支持按命令/设备/状态做快速检索，便于在长历史中快速定位目标命令。
  - 证据：`GET /netdiag` 冒烟通过；前端工作台渲染逻辑已落地
- 终端区命中可见性：检索结果已支持高亮展示，降低长列表定位成本。
  - 证据：`GET /netdiag` 冒烟通过；前端工作台渲染逻辑已落地
- 工程师式追问：主路径高频缺参提示已改为工程师式追问，不再直接报“缺字段”。
  - 证据：`GET /netdiag` 冒烟通过；主模板文案已更新
- 下一轮目标承接：`next_round` 的目标验证提示已注入后续 `plan`，不再只是 UI 文本提示。
  - 证据：主模板逻辑已落地；相关主流程回归通过
- 下一轮结构化目标：`next_round` 已升级为结构化 `target_probe`，会进入 `plan` API、持久化到 round，并在刷新后重新恢复。
  - 证据：`app/routers/netdiag.py`、`app/templates/netdiag_home.html`、`tests/test_plan_fast_deterministic.py`
- 下一轮选步定向：`target_probe` 已可携带 `preferred_intents / expected_signals`，直接影响 `plan` 选步优先级。
  - 证据：`app/diagnosis/sop_engine.py`、`tests/test_sop_engine.py`
- 下一轮任务字段：`target_probe` 已补 `hypothesis_id / stop_if_matched`，开始向统一验证任务对象收敛。
  - 证据：`app/routers/netdiag.py`、`app/templates/netdiag_home.html`；`GET /netdiag` 冒烟通过
- 下一轮任务对象增强：`target_probe` 已补 `expected_evidence / stop_reason / preferred_scope`，并开始把未命中信号传到 SOP 步骤与 PlannedCommand 的 `expected_signal`。
  - 证据：`app/routers/netdiag.py`、`app/diagnosis/sop_engine.py`、`tests/test_sop_engine.py`
- 单枪验证预算：`stop_if_matched=true` 的验证任务已会压成单枪预算，降低外层“大轮次计划”感。
  - 证据：`app/routers/netdiag.py`、`tests/test_plan_fast_deterministic.py`
- 任务对象进入收敛链：`expected_evidence / stop_reason` 已进入分析与收敛逻辑，命中后可按验证任务对象定义的动作收敛。
  - 证据：`app/routers/netdiag.py`、`tests/test_analyze_fastpath.py`
- 任务对象进入工作台默认行为：`preferred_scope=related_commands` 已可驱动终端默认聚焦到“相关视图”。
  - 证据：`app/templates/netdiag_home.html`；`GET /netdiag` 冒烟通过
- 相关视图聚类：终端区在 `相关视图` 下已可按验证意图/期望证据分组，降低相关命令平铺带来的检索成本。
- 动作语义脱敏：前端已把 `next_round` 归一成用户侧“继续验证”，顶部状态和历史会话列表也已改为友好状态文案，不再直接暴露 `need_next_round`。
  - 证据：`app/templates/netdiag_home.html`；`GET /netdiag` 冒烟通过
- 动作/状态元数据下沉：后端 `session/next_action` 已提供 `ui_status / ui_action`，前端后续重构可以直接消费，不再依赖每个组件重复翻译内部状态。
  - 证据：`app/routers/netdiag.py`、`tests/test_next_action_conclude.py`
- 下一枪目标后端化：分析阶段已开始直接生成 `evidence_overview.next_target_probe`，前端刷新/恢复时优先使用服务端持久化目标，不再只靠浏览器端重建。
  - 证据：`app/routers/netdiag.py`、`app/templates/netdiag_home.html`、`tests/test_analyze_fastpath.py`
- 下一枪目标主链路化：`session / next_action` 已直接返回 `next_target_probe`，前端“继续验证”与会话恢复优先消费服务端动作输入，不再主导拼装下一枪目标。
  - 证据：`app/routers/netdiag.py`、`app/templates/netdiag_home.html`、`tests/test_next_action_conclude.py`
- 统一验证任务对象：分析阶段已开始生成 `validation_task`，把 `current_probe / next_probe / expected_signal_review / uncovered_goals / stop_reason` 收敛到单一结构，前端分析区与终端关联上下文已开始优先消费它。
  - 证据：`app/routers/netdiag.py`、`app/templates/netdiag_home.html`、`tests/test_analyze_fastpath.py`
- 统一任务对象进入计划链：`validation_task` 已进入 `plan` API 主链路，前端继续验证时优先把该对象发回后端，后端再从中提取下一枪目标，不再要求前端先拆回 `target_probe`。
  - 证据：`app/routers/netdiag.py`、`app/templates/netdiag_home.html`、`tests/test_plan_fast_deterministic.py`
- 统一任务对象接管恢复与工作台：后端会优先从 `validation_task.next_probe` 恢复下一枪目标，前端分析区/终端区/继续验证入口统一优先消费 `validation_task`，进一步削减 `target_probe / expected_signal_review` 双轨读取。
  - 证据：`app/routers/netdiag.py`、`app/templates/netdiag_home.html`、`tests/test_analyze_fastpath.py`
- 动作语义进入后端主链：`next_action.action` 已切换为 `continue_probe`，同时保留 `raw_action=next_round` 兼容层，后端工作流输出已开始摆脱“大轮次”命名。
  - 证据：`app/routers/netdiag.py`、`tests/test_next_action_conclude.py`
- `continue_probe` 已进入工作流许可：`plan_round` 现在直接接受 `continue_probe` 作为合法下一步；同时 `session dump / next_action` 已共用同一份下一枪 payload，降低恢复/动作卡目标不一致的风险。
  - 证据：`app/routers/netdiag.py`、`tests/test_next_action_conclude.py`
- 终端区验证焦点排序：终端区已开始按 `validation_task` 自动提升未命中信号/优先意图/当前验证目标命中的命令排序，默认选中也会优先落到这些高价值命令。
  - 证据：`app/templates/netdiag_home.html`；`GET /netdiag` 冒烟通过
- 终端区自动聚焦：当前验证任务声明 `preferred_scope=related_commands` 且存在相关命令时，终端区会默认自动切到“相关视图”；前端分析区/终端区也已继续削减对旧 `target_probe/next_target_probe` 的直接读取。
  - 证据：`app/templates/netdiag_home.html`；`GET /netdiag` 冒烟通过
- 服务端 round dump 标准化：`GET /sessions/{sid}` 返回的 `rounds[*].evidence_overview` 已自带标准化 `validation_task / next_target_probe`，前端恢复历史 round 时不再需要额外从旧字段推断。
  - 证据：`app/routers/netdiag.py`、`tests/test_next_action_conclude.py`
- 前端验证任务主源继续收敛：`_resolveValidationTask(...)` 在 round/session 已带标准化 `validation_task` 时，不再优先回读旧 `target_probe / next_target_probe / expected_signal_review` 字段。
  - 证据：`app/templates/netdiag_home.html`；`GET /netdiag` 冒烟通过
- 下一枪目标推导继续收敛：前端 `_buildNextTargetProbe(...)` 已改为优先基于 `validation_task` 推导下一枪目标，不再优先直读旧 `next_target_probe / expected_signal_review` 字段。
  - 证据：`app/templates/netdiag_home.html`；`GET /netdiag` 冒烟通过
- 展示层状态语义继续收敛：顶部步骤条与“继续验证”同步逻辑已开始优先消费 `ui_status`，减少页面展示路径对原始 `need_next_round` 的直接判断。
  - 证据：`app/templates/netdiag_home.html`；`GET /netdiag` 冒烟通过
- 前端“待继续验证”判断已统一：新增 `_sessionReadyForNextProbe(...)`，顶部状态、继续验证目标同步、按钮启用条件开始共用同一套状态判断，减少前端状态双轨。
  - 证据：`app/templates/netdiag_home.html`；`GET /netdiag` 冒烟通过
- 终端区验证任务聚类：`相关视图` 下已开始按“未命中信号 / 优先意图 / 已命中信号 / 其他”分组与排序，当前验证任务对命令列表的收束更稳定。
  - 证据：`app/templates/netdiag_home.html`；`GET /netdiag` 冒烟通过
- 终端区自动收束：当验证任务要求 `related_commands` 且用户未搜索时，`相关视图` 会默认收束到最高价值簇，优先展示最关键的未命中/优先意图命令，降低噪声。
  - 证据：`app/templates/netdiag_home.html`；`GET /netdiag` 冒烟通过
- 终端区最优回放焦点：在自动收束场景下，终端区不再优先恢复旧选中项，而是优先锁定当前最高价值的运行中/待执行/高焦点命令。
  - 证据：`app/templates/netdiag_home.html`；`GET /netdiag` 冒烟通过
- Continue-probe payload 去冗余：后端 `_session_continue_probe_payload(...)` 已去掉冗余 fallback，下一枪 payload 继续收向 `validation_task` 单一路径。
  - 证据：`app/routers/netdiag.py`、`tests/test_next_action_conclude.py`
- Canonical action 收敛：后端 `continue_probe` 已成为 canonical action，`next_action.raw_action` 直接返回 `continue_probe`，旧 `next_round` 仅以 `legacy_action` 兼容字段保留。
  - 证据：`app/routers/netdiag.py`、`tests/test_next_action_conclude.py`
- Round 响应标准化：`plan` 期 round 已直接持久化标准化 `validation_task / next_target_probe`，且 round 相关 API 响应统一走 `_round_response_payload(...)`，避免计划期和分析期结构不一致。
  - 证据：`app/routers/netdiag.py`、`app/diagnosis/session_manager.py`、`tests/test_plan_fast_deterministic.py`、`tests/test_analyze_fastpath.py`
- 终端范围自动选择：终端区未手工锁定时，已开始按验证任务在 `related/current/all` 之间自动切到最佳范围；用户手工切换后则按会话记住并尊重该选择。
  - 证据：`app/templates/netdiag_home.html`；`GET /netdiag` 冒烟通过
- Validation-task 主源继续收紧：前端 `_resolveValidationTask(...)` 主路径已不再直接回读 `session.next_target_probe / next_action.target_probe`，统一优先消费标准化 `validation_task`。
  - 证据：`app/templates/netdiag_home.html`；`GET /netdiag` 冒烟通过
- Validation-task 补齐收口：当前端已拿到 `validation_task` 但字段不完整时，会优先从 round 的 `target_probe / next_target_probe / focus_review / stop_decision` 补齐，而不是让其他 helper 再各自回退旧字段。
  - 证据：`app/templates/netdiag_home.html`；`GET /netdiag` 冒烟通过
- 最佳焦点持续保持：终端区在自动范围场景下已开始忽略历史选中项，优先固定到当前最佳命令，避免旧焦点反复抢回。
  - 证据：`app/templates/netdiag_home.html`；`GET /netdiag` 冒烟通过
- Validation-task 后端补齐：round 级标准化已可在持久化 `validation_task` 字段不完整时，自动用 `target_probe / next_target_probe / focus_review / stop_decision / expected_signal_review` 补齐，减少前端主链重复补洞。
  - 证据：`app/routers/netdiag.py`、`tests/test_analyze_fastpath.py`
- Validation-task 前端主链收口：`_resolveValidationTask(...)` 当拿到标准化 `validation_task` 时直接使用该对象，旧字段回读只留给历史兼容兜底。
  - 证据：`app/templates/netdiag_home.html`；`GET /netdiag` 冒烟通过
- 终端焦点稳定性：自动范围切换场景下，终端区已开始持续保持最佳焦点，旧选中项不会再把当前最高价值命令抢回。
  - 证据：`app/templates/netdiag_home.html`；`GET /netdiag` 冒烟通过
- 终端范围稳定性：未手工锁定过滤器时，终端区会持续记住上一轮自动选出的最佳范围；只要该范围仍有效就继续沿用，不会在 `related/current/all` 之间无意义抖动。
  - 证据：`app/templates/netdiag_home.html`；`GET /netdiag` 冒烟通过
- Continue-probe 计划链收口：继续验证发起计划时，前端若已有标准化 `validation_task` 则不再强制同时提交独立 `target_probe`；后端优先从统一任务对象提取下一枪目标。
  - 证据：`app/templates/netdiag_home.html`、`app/routers/netdiag.py`
- Validation-task 落盘补齐：`plan_round / analyze_round` 当前落盘的 `validation_task` 已统一经过上下文补齐，减少会话恢复和继续验证再从旧字段二次拼装。
  - 证据：`app/routers/netdiag.py`、`tests/test_analyze_fastpath.py`
- UI 状态语义下沉：后端 `ui_status` 已开始按 workflow action 推导，当下一步为 `continue_probe` 时，会稳定返回 `ready_for_next_probe`，而不再只是 raw `status` 的被动映射。
  - 证据：`app/routers/netdiag.py`、`tests/test_next_action_conclude.py`
- Raw 状态下沉：后端已开始在可继续验证的直接路径上真实写入 `ready_for_next_probe`，不再把所有“可继续验证”状态都压回旧 `need_next_round`。
  - 证据：`app/routers/netdiag.py`、`app/diagnosis/models.py`、`tests/test_next_action_conclude.py`
- 前端信号评审双轨削减：终端区已不再额外传递 `expected_signal_review`，统一从 `validation_task` 派生信号评审与焦点排序。
  - 证据：`app/templates/netdiag_home.html`；回归通过
- 内部状态口径切换：项目内部测试与回归口径已开始切到 raw `ready_for_next_probe`，进一步压缩 `need_next_round` 的实际使用面。
  - 证据：`tests/test_next_action_conclude.py`、`tests/test_plan_fast_deterministic.py`、`tests/test_analyze_stale_recovery.py`
- 信号分层聚类：终端区在 `相关视图` 下已可区分“未命中信号 / 已命中信号 / 意图”分层，进一步贴近验证闭环。
  - 证据：`app/templates/netdiag_home.html`；`GET /netdiag` 冒烟通过
- 预期信号评审：`expected_signals` 已进入分析层，输出 `matched / unmatched / coverage_ratio`，并写入 `evidence_overview`。
  - 证据：`app/routers/netdiag.py`、`tests/test_analyze_fastpath.py`
- 预期信号收敛修正：`expected_signals` 全命中时可推动 `conclude_with_verification`，部分命中时会提升置信度。
  - 证据：`app/routers/netdiag.py`、`tests/test_analyze_fastpath.py`
- 右侧分析固定摘要：分析显示区已固定显示 `target_probe / expected_signal_review / 当前判定`，降低用户对聊天折叠详情的依赖。
  - 证据：`app/templates/netdiag_home.html`；`GET /netdiag` 冒烟通过
- 分析到终端弱联动：当前验证目标已可给终端区相关命令打高亮，降低用户在历史命令中检索成本。
  - 证据：`app/templates/netdiag_home.html`；`GET /netdiag` 冒烟通过
- 分析到终端跳转：右侧分析区已支持“一键跳转到相关命令/结果”，可直接把终端焦点带到当前验证目标最相关的命令和回显。
  - 证据：`app/templates/netdiag_home.html`；`GET /netdiag` 冒烟通过
- 分析到终端相关视图：终端区已支持 `相关视图` 过滤，可直接聚焦当前验证目标相关命令集合。
  - 证据：`app/templates/netdiag_home.html`；`GET /netdiag` 冒烟通过

### 6.2 还没有完成
- `G4 不重复执行`：后端已有去重能力，但 UI 侧“历史命令/本轮命令/终端回放”统一展示仍未完全重构，验收口径暂只到 API/回归脚本，不到最终工作台体验。
- `G8 UI 一致性`：右侧虽已完成第一阶段分工，但“上分析区、下终端区”更强联动与终端回放体验仍未完全收口。
- 轮次循环仍以 `plan -> approve -> execute -> analyze` 为外层骨架，尚未完全收敛为“下一枪验证式”的单步假设循环。
- 工程师式追问仍有残余字段化文案，尚未做到所有路径统一。
- `target_probe` 虽已进入主链路并补齐更多任务字段，但还没与完整结构化证据层合并成统一验证任务对象。
- `expected_signals` 已开始进入停止条件，但还没真正并入完整统一证据模型。
- 分析显示区虽已固定显示结构化摘要，但和终端区的联动仍未达到“按目标自动聚类”的深度。
- 终端区虽已有相关命令高亮、一键跳转和 `相关视图` 过滤，但仍缺更深的目标驱动聚类。

### 6.3 风险
- 前端工作台重构尚未完成前，用户对“当前动作来源”和“右侧展示来源”的理解成本仍偏高。
- `minimal_probe_budget` 已收敛首轮命令数，但若后续假设层扩张失控，仍可能回退到偏大计划。
- 结论块已标准化，但如果新路径绕开统一分析出口，可能出现“状态更新有了、结论块缺失”的回归。

### 6.4 下一阶段
1. 把 `next_round` 改成“下一枪验证”而不是重新走一轮大计划。
2. 完成右侧工作台重构：上方分析显示区、下方终端回显区。
3. 清理残余字段化追问，让 AI 对话完全转成工程师式追问。
- Validation-task 输入主源继续收口：`plan_round` 已优先从 `validation_task` 提取下一枪目标，独立 `target_probe` 退回兼容兜底。
  - 证据：`app/routers/netdiag.py`、`tests/test_plan_fast_deterministic.py`
- 终端相关命令簇持续保持：自动收束场景下会记住并优先恢复上一轮自动选中的最佳相关命令簇，只要该簇仍有效就继续沿用。
  - 证据：`app/templates/netdiag_home.html`；`GET /netdiag` 冒烟通过
- 终端自动回放焦点持续保持：自动聚焦场景下会记住上一轮自动选中的最佳命令，只要该命令仍有效就继续沿用，不再因为刷新或继续验证而跳回旧手工选中项。
  - 证据：`app/templates/netdiag_home.html`；`GET /netdiag` 冒烟通过
- 边缘字段化提示继续清理：时间窗不明确的追问已改成工程师式提示，说明 AI 将先按安全默认时间窗启动并用设备证据收敛。
  - 证据：`app/templates/netdiag_home.html`；`GET /netdiag` 冒烟通过
- Validation-task 前端主源已收口：`_resolveValidationTask(...)` 现在只消费标准化 `validation_task`，主链不再从旧 `target_probe / next_target_probe / expected_signal_review` 现拼任务对象。
  - 证据：`app/templates/netdiag_home.html`；`GET /netdiag` 冒烟通过
- Validation-task 后端主源继续收口：`期望信号评审 / 收敛判定修正 / 下一枪目标推导` 已开始优先消费统一 `validation_task`，不再先依赖旧 `target_probe / expected_signal_review`。
  - 证据：`app/routers/netdiag.py`、`tests/test_analyze_fastpath.py`
- Continue-probe 兼容语义继续收口：前端下一枪目标 `source` 已统一改为 `continue_probe`，继续验证计划请求只在缺少 `validation_task` 时才附带兼容 `target_probe`。
  - 证据：`app/templates/netdiag_home.html`、`tests/test_plan_fast_deterministic.py`、`tests/test_next_action_conclude.py`
- 前端状态/动作兼容别名继续收口：动作卡、自动推进、步骤条已不再显式依赖 `next_round / need_next_round`，旧别名只保留在归一化兼容入口。
  - 证据：`app/templates/netdiag_home.html`、回归测试通过
- 历史会话状态兼容继续收口：`session_manager` 在载入持久化会话和 `set_status()` 时，会把旧 `need_next_round` 统一归一成 raw `ready_for_next_probe`。
  - 证据：`app/diagnosis/session_manager.py`、`tests/test_session_manager_status_normalization.py`
- 主状态集合已统一：`need_next_round` 已从工程主状态集合中移除，项目内部主状态统一为 raw `ready_for_next_probe`，旧状态仅保留在兼容归一化层。
  - 证据：`app/diagnosis/models.py`、`app/diagnosis/session_manager.py`、`tests/test_session_manager_status_normalization.py`
- 前端下一枪目标构建已收口：`_buildNextTargetProbe(...)` 现在只消费标准化 `validation_task`，不再从旧 round 字段本地二次推断。
  - 证据：`app/templates/netdiag_home.html`、回归测试通过
- 前端继续验证提交流已收口：正常主链现在只提交 `validation_task`，不再主动发送兼容 `target_probe`。
  - 证据：`app/templates/netdiag_home.html`、回归测试通过
