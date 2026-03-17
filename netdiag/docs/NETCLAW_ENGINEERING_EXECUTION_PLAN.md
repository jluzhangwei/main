# NetClaw 工程实施计划（AI 工程师版）

## 1. 使用方式
AI 工程师在实施前必须先阅读：
1. `docs/NETCLAW_ARCHITECT_MASTER_PLAN.md`
2. `docs/NETCLAW_AGENT_REQUIREMENTS.md`
3. `docs/NETCLAW_ARCHITECT_ACCEPTANCE_CHECKLIST.md`

## 2. 实施顺序
### EPIC 1：对话主导
- E1-1 对话上下文合并解析
- E1-2 缺参提示改成工程师式追问
- E1-3 时间窗默认启动与后端兜底
- E1-4 新会话 / 当前会话 / 历史会话切换一致化

### EPIC 2：诊断内核
- E2-1 假设对象标准化
- E2-2 计划器改为最小命令集
- E2-3 证据抽取统一格式
- E2-4 本轮结论强制输出
- E2-5 直接证据快速收敛

### EPIC 3：工作台
- E3-1 对话区只保留会话交互
- E3-2 分析区接管详情展示
- E3-3 终端区拆成命令列表 + 回显面板
- E3-4 动作按钮只存在于对话动作卡

### EPIC 4：知识与性能
- E4-1 Fast Path / Deep Path 分流
- E4-2 模型路由与失败回退
- E4-3 案例库/已知问题库低权重注入
- E4-4 性能埋点与耗时归因

## 3. 每个任务的完成定义
每个任务完成时，AI 工程师必须提交：
1. 修改文件列表
2. 行为变化说明
3. 回归测试
4. 未完成项 / 风险项

## 4. 实施约束
1. 不允许引入写命令
2. 不允许把前端状态机做成独立真相源，必须服从后端 `next_action`
3. 不允许因为 UI 方便而绕开证据层
4. 不允许用硬编码单案例规则代替通用证据抽取
5. 不允许把“存在代码”当作“问题已解决”

## 5. AI 工程师交付模板
### 5.1 变更摘要
- 解决了什么问题
- 为什么这样改
- 影响哪些流程

### 5.2 验证摘要
- 单测
- API 冒烟
- 真实设备或仿真验证

### 5.3 剩余风险
- 哪些还未闭环
- 哪些只做了局部验证

## 6. 优先级
### P0
- 对话上下文合并
- 默认时间窗启动
- 假设驱动最小命令集
- 本轮结论输出
- 已有直接证据立即收敛

### P1
- 分析区/终端区重构
- 命令列表回看
- 性能埋点
- 失败回退体验

### P2
- 多设备跳转
- 动态时间窗
- 更强的经验库策略

## 7. 当前实施状态（2026-03-12）
### 7.1 已完成
- `E1-1` 对话上下文合并解析
- `E1-3` 时间窗默认启动与后端兜底
- `E2-2` 计划器改为最小命令集（已完成第一阶段：`minimal_probe_budget`）
- `E2-4` 本轮结论强制输出
- `E2-5` 直接证据快速收敛
- `E4-2` 模型路由与失败回退
- `E3-2` 分析区接管详情展示（已完成第一阶段：聊天详情自动投送右侧显示区）
- `E3-4` 动作/详情从聊天卡收敛到右侧显示区（已完成第一阶段）
- `E3-3` 终端区拆成命令列表 + 回显面板（已完成第二阶段：跨轮次历史显示 + 当前会话选中记忆）
- `E3-3` 终端区拆成命令列表 + 回显面板（已完成第三阶段：按轮次过滤 + 全部轮次分组头）
- `E3-3` 终端区拆成命令列表 + 回显面板（已完成第四阶段：快速检索）
- `E3-3` 终端区拆成命令列表 + 回显面板（已完成第五阶段：检索高亮）
- `E1-2` 缺参提示改成工程师式追问（已完成第二阶段：主路径高频提示收敛）
- `E2-2` 计划器改为最小命令集（已完成第二阶段：下一轮隐式目标注入到 `plan`）
- `E2-2` 计划器改为最小命令集（已完成第三阶段：下一轮结构化 `target_probe` 注入 `plan` 并持久化到 round）
- `E2-2` 计划器改为最小命令集（已完成第四阶段：`target_probe` 携带 `preferred_intents / expected_signals` 并直接参与选步）
- `E2-2` 计划器改为最小命令集（已完成第五阶段：下一枪目标会利用 `unmatched expected_signals` 反推 `preferred_intents`）
- `E2-2` 计划器改为最小命令集（已完成第六阶段：`target_probe` 已补 `hypothesis_id / stop_if_matched` 字段）
- `E2-2` 计划器改为最小命令集（已完成第七阶段：`target_probe` 已补 `expected_evidence / stop_reason / preferred_scope` 字段）
- `E2-2` 计划器改为最小命令集（已完成第八阶段：未命中信号开始进入 SOP 步骤和 PlannedCommand 的 `expected_signal`）
- `E2-2` 计划器改为最小命令集（已完成第九阶段：`stop_if_matched=true` 的验证任务会压成单枪预算）
- `E2-3` 证据抽取统一格式（已完成第三阶段：`expected_evidence / stop_reason` 已进入分析与收敛链路）
- `E3-3` 终端区拆成命令列表 + 回显面板（已完成第九阶段：`preferred_scope=related_commands` 会驱动默认“相关视图”）
- `E3-3` 终端区拆成命令列表 + 回显面板（已完成第十阶段：`相关视图` 会按验证意图/期望证据分组）
- `E3-3` 终端区拆成命令列表 + 回显面板（已完成第十一阶段：`相关视图` 会按未命中/已命中信号分层）
- `E2-3` 证据抽取统一格式（已完成第一阶段：`expected_signals` 命中评审写入分析结果与 `evidence_overview`）
- `E3-1` 对话主导工作流（已完成第三阶段：前端已引入 `continue_probe` 语义别名，用户侧状态/动作不再直接暴露 `next_round / need_next_round`）
- `E3-1` 对话主导工作流（已完成第四阶段：后端 `session/next_action` 已补 `ui_status / ui_action` 元数据，前端不再需要分散翻译内部动作名）
- `E2-2` 计划器改为最小命令集（已完成第十阶段：下一枪目标已开始由后端分析阶段直接生成并写入 `evidence_overview.next_target_probe`）
- `E3-1` 对话主导工作流（已完成第五阶段：`session / next_action` 已直接携带 `next_target_probe`，前端恢复会话与继续验证优先消费服务端目标）
- `E2-3` 证据抽取统一格式（已完成第四阶段：分析阶段已开始生成统一 `validation_task`，把当前验证目标/下一枪目标/信号评审/未覆盖目标收敛到单一对象）
- `E2-2` 计划器改为最小命令集（已完成第十一阶段：`validation_task` 已进入 `plan` 主链路，后端可直接从统一任务对象提取下一枪 `target_probe`）
- `E2-3` 证据抽取统一格式（已完成第五阶段：后端已优先从 `validation_task.next_probe` 恢复下一枪目标，前端分析区/终端区/继续验证入口统一优先消费 `validation_task`）
- `E1-4` 动作状态机语义收敛（已完成第一阶段：后端 `next_action.action` 已切到 `continue_probe`，并保留 `raw_action=next_round` 兼容层）
- `E1-4` 动作状态机语义收敛（已完成第二阶段：`continue_probe` 已进入 `plan_round` 工作流许可链，且 `session dump / next_action` 的下一枪 payload 已统一共用同一条生成链）
- `E3-3` 终端区拆成命令列表 + 回显面板（已完成第十二阶段：终端区已开始按 `validation_task` 自动做验证焦点排序，未命中信号/优先意图会优先排前）
- `E3-3` 终端区拆成命令列表 + 回显面板（已完成第十三阶段：当前验证任务要求 `related_commands` 时，终端区会默认自动切到“相关视图”；前端分析区/终端区继续减少对旧 `target_probe/next_target_probe` 字段的直接读取）
- `E2-3` 证据抽取统一格式（已完成第六阶段：服务端 `session dump` 会为 `rounds[*].evidence_overview` 注入标准化 `validation_task / next_target_probe`，前端恢复历史 round 时不再临时拼装旧字段）
- `E2-3` 证据抽取统一格式（已完成第七阶段：前端 `_resolveValidationTask(...)` 已进一步收紧为 `validation_task` 主源，只有任务对象缺失时才回退旧字段）
- `E1-4` 动作状态机语义收敛（已完成第三阶段：顶部步骤条与“继续验证”同步逻辑已开始优先消费 `ui_status`，减少展示层对原始 `need_next_round` 的直接依赖）
- `E3-3` 终端区拆成命令列表 + 回显面板（已完成第十四阶段：终端区新增验证任务聚类元数据，`相关视图` 下会按“未命中信号 / 优先意图 / 已命中信号 / 其他”自动分组排序）
- `E2-3` 证据抽取统一格式（已完成第八阶段：前端 `_buildNextTargetProbe(...)` 已改为优先基于 `validation_task` 推导下一枪目标，不再优先直读旧 `next_target_probe / expected_signal_review`）
- `E3-3` 终端区拆成命令列表 + 回显面板（已完成第十五阶段：`相关视图` 会在默认场景下自动收束到最高价值簇，优先展示当前验证任务最关键的未命中/优先意图命令）
- `E1-4` 动作状态机语义收敛（已完成第四阶段：前端新增 `_sessionReadyForNextProbe(...)`，顶部状态、继续验证目标同步、按钮启用条件共用同一套“待继续验证”判断）
- `E3-3` 终端区拆成命令列表 + 回显面板（已完成第十六阶段：自动收束场景下不再优先恢复旧选中项，而是固定最优回放焦点，优先锁定当前最高价值命令）
- `E2-3` 证据抽取统一格式（已完成第九阶段：后端 `_session_continue_probe_payload(...)` 已去掉冗余 fallback，下一枪 payload 继续收向 `validation_task`）
- `E1-4` 动作状态机语义收敛（已完成第五阶段：后端 `continue_probe` 已成为 canonical action，`next_round` 仅以 `legacy_action` 兼容保留）
- `E2-3` 证据抽取统一格式（已完成第十阶段：`plan` 期 round 已直接持久化标准化 `validation_task / next_target_probe`，且 round 相关 API 响应统一走 `_round_response_payload(...)`）
- `E3-3` 终端区拆成命令列表 + 回显面板（已完成第十七阶段：终端区已开始按验证任务自动选择最佳过滤范围；未手工锁定时会在 `related/current/all` 间自动落到最合适视图）
- `E2-3` 证据抽取统一格式（已完成第十一阶段：前端 `_resolveValidationTask(...)` 主路径已不再直接回读 `session.next_target_probe / next_action.target_probe`，统一优先消费标准化 `validation_task`）
- `E2-3` 证据抽取统一格式（已完成第十二阶段：前端 `_resolveValidationTask(...)` 会在已有 `validation_task` 但字段不完整时，优先用 round 的 `target_probe / next_target_probe / focus_review / stop_decision` 补齐统一任务对象）
- `E3-3` 终端区拆成命令列表 + 回显面板（已完成第十八阶段：终端区在自动范围场景下已开始持续保持最佳焦点，未手工锁定时不会再被历史选中项抢回）
- `E1-4` 动作状态机语义收敛（已完成第六阶段：后端 `ui_status` 已开始按 workflow action 推导，当下一步为 `continue_probe` 时会稳定返回 `ready_for_next_probe`，不再只靠 raw `status` 映射）
- `E1-4` 动作状态机语义收敛（已完成第七阶段：后端已开始在可继续验证的直接路径上真实写入 raw `ready_for_next_probe`，包括 Baseline 成功/复用、Resume、Execute 完成、Analyze 完成及部分 stale recovery）
- `E2-3` 证据抽取统一格式（已完成第十三阶段：终端区已取消额外 `expected_signal_review` 传递，统一从 `validation_task` 派生信号评审，继续减少前端双轨状态）
- `E1-4` 动作状态机语义收敛（已完成第八阶段：项目内部测试与回归口径已开始切到 raw `ready_for_next_probe`，进一步减少 `need_next_round` 作为主状态的使用面）
- `E2-3` 证据抽取统一格式（已完成第十四阶段：后端 round 级 `validation_task` 标准化已可在持久化对象字段不完整时，自动用 `target_probe / next_target_probe / focus_review / stop_decision / expected_signal_review` 补齐）
- `E2-3` 证据抽取统一格式（已完成第十五阶段：前端 `_resolveValidationTask(...)` 主链已进一步收紧为直接消费标准化 `validation_task`，旧字段只保留历史兼容兜底）
- `E3-3` 终端区拆成命令列表 + 回显面板（已完成第十九阶段：终端区在自动范围切换时已开始持续保持最佳焦点，旧选中项不会再把当前最高价值命令焦点抢回）
- `E3-3` 终端区拆成命令列表 + 回显面板（已完成第二十阶段：未手工锁定过滤器时，终端区会持续记住上一轮自动选出的最佳范围，只要该范围仍有效就继续沿用，减少 `related/current/all` 之间反复抖动）
- `E2-3` 证据抽取统一格式（已完成第十六阶段：继续验证发起计划时，前端若已有标准化 `validation_task` 则不再强制同时提交独立 `target_probe`，后端优先从统一任务对象提取下一枪目标）
- `E2-3` 证据抽取统一格式（已完成第十七阶段：`plan_round / analyze_round` 当前落盘的 `validation_task` 已统一经过上下文补齐，减少后续恢复/继续验证再从旧字段二次拼装的需要）
- `E3-3` 终端区拆成命令列表 + 回显面板（已完成第二十一阶段：`related_commands` 自动收束场景下，终端区会持续记住上一轮自动选中的最佳簇，只要该簇仍有效就继续沿用）
- `E4-2` 对话引导文案（已完成第一阶段：缺少时间窗时的边缘追问已改成人话化表达，优先引导用户给出大概时间或允许 AI 先按安全默认时间窗启动）
- `E2-3` 证据抽取统一格式（已完成第二阶段：`expected_signals` 命中结果开始影响 `stop_decision`）
- `E3-2` 分析区接管详情展示（已完成第二阶段：右侧固定显示 `target_probe / expected_signal_review / 当前判定`）
- `E3-3` 终端区拆成命令列表 + 回显面板（已完成第六阶段：按当前验证目标高亮相关命令）
- `E3-3` 终端区拆成命令列表 + 回显面板（已完成第七阶段：从右侧分析区一键跳转到相关命令/结果）
- `E3-3` 终端区拆成命令列表 + 回显面板（已完成第八阶段：支持 `相关视图` 过滤，直接聚焦当前验证目标相关命令）
- `E3-4` 动作/详情从聊天卡收敛到右侧显示区（已完成第二阶段：用户侧 `next_round` 展示改为“下一枪验证 / Next Probe”）

### 7.2 部分完成
- `E1-2` 缺参提示改成工程师式追问
  - 现状：主路径高频提示已改，残余字段化文案仍在少量边缘路径存在。
- `E1-4` 新会话 / 当前会话 / 历史会话切换一致化
  - 现状：主路径可用，但仍需继续压缩边缘状态与刷新后的展示差异。
- `E3-3` 终端区拆成命令列表 + 回显面板
  - 现状：基础结构、跨轮次历史显示、当前会话选中记忆、按轮次过滤、快速检索已落地；但更强回放检索与聚类仍需继续打磨。
- `E3-2` 分析区接管详情展示
  - 现状：右侧已固定显示当前验证目标/预期信号评审/当前判定，但还未和终端区做深度联动高亮。
- `E3-3` 终端区拆成命令列表 + 回显面板
  - 现状：已能根据当前验证目标高亮相关命令，并支持从右侧分析区一键跳转与 `相关视图` 过滤；但还缺更深的目标驱动聚类。
- `E2-2` 计划器改为最小命令集
  - 现状：`target_probe` 已进入主链路、可刷新恢复、可携带 `preferred_intents / expected_signals` 驱动选步，且已开始利用未命中信号反推下一枪意图，并补齐 `hypothesis_id / stop_if_matched / expected_evidence / stop_reason / preferred_scope` 字段；但尚未变成完整结构化验证任务对象。
- `E2-3` 证据抽取统一格式
  - 现状：`expected_signals` 已有轻量命中评审，并开始影响 `stop_decision`；但还未纳入完整统一证据判定。

### 7.3 未完成
- `E2-1` 假设对象标准化到完整统一对象
- `E2-3` 证据抽取统一格式
- `E3-1` 对话区只保留会话交互
- `E4-1` Fast Path / Deep Path 分流完整化
- `E4-3` 案例库/已知问题库低权重注入完善
- `E4-4` 性能埋点与耗时归因
- `E2-3` 证据抽取统一格式（已完成第十八阶段：`plan_round` 已优先从 `validation_task` 提取 `target_probe`，独立 `target_probe` 退回兼容输入兜底）
- `E3-3` 终端区拆成命令列表 + 回显面板（已完成第二十一阶段：自动收束场景下会持续记住上一轮自动选中的最佳相关命令簇，只要该簇仍有效就继续沿用）
- `E3-3` 终端区拆成命令列表 + 回显面板（已完成第二十二阶段：自动聚焦场景下会持续记住上一轮自动选中的最佳命令，只要该命令仍有效就继续沿用，刷新/继续验证后不再轻易跳焦）
- `E3-1` 对话主导工作流（已完成第六阶段：边缘时间窗追问已改成工程师式提示，AI 会先按安全默认时间窗启动再收敛，不再直出字段式表达）
- `E2-3` 证据抽取统一格式（已完成第十九阶段：前端 `_resolveValidationTask(...)` 已切到只消费标准化 `validation_task`，主链不再从旧 `target_probe / next_target_probe / expected_signal_review` 现拼任务对象）
- `E2-3` 证据抽取统一格式（已完成第二十阶段：后端 `期望信号评审 / 收敛判定修正 / 下一枪目标推导` 已开始优先消费 `validation_task`，减少 helper 内部对旧 `target_probe / expected_signal_review` 的直接依赖）
- `E1-4` 动作状态机语义收敛（已完成第九阶段：前端下一枪目标 `source` 已统一改为 `continue_probe`，继续验证计划请求仅在缺少 `validation_task` 时才附带兼容 `target_probe`）
- `E1-4` 动作状态机语义收敛（已完成第十阶段：前端动作卡、自动推进、步骤条已去掉对 `next_round / need_next_round` 的显式依赖，统一按 `continue_probe / ready_for_next_probe` 主口径工作）
- `E1-4` 动作状态机语义收敛（已完成第十一阶段：`session_manager` 在加载持久化会话和 `set_status()` 时，会把旧 `need_next_round` 统一归一成 raw `ready_for_next_probe`，旧主状态不再重新进入内存主链）
- `E1-4` 动作状态机语义收敛（已完成第十二阶段：`need_next_round` 已从工程主状态集合中移除，项目内部主状态统一为 raw `ready_for_next_probe`，旧状态只留在兼容归一化层）
- `E2-3` 证据抽取统一格式（已完成第二十一阶段：前端 `_buildNextTargetProbe(...)` 已切到仅消费标准化 `validation_task`，不再从旧 round 字段本地二次推断下一枪目标）
- `E2-3` 证据抽取统一格式（已完成第二十二阶段：前端继续验证提交流已完全改为只提交 `validation_task`，正常主链不再主动发送兼容 `target_probe`）
