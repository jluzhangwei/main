# NetClaw Agent Mode 需求文档（V1）

## 1. 文档目标
把 `NetClaw` 从“表单驱动的诊断工具”升级为“对话驱动的网络工程师代理”。

目标不是让用户配合系统填字段，而是：
- 用户描述现象
- AI 主动理解上下文
- AI 主动追问必要信息
- AI 主动执行只读诊断命令
- AI 基于设备证据逐轮收敛根因

本版本只定义可落地、可验证、可逐步实现的 Agent 模式，不引入写配置能力。

## 2. 产品原则
1. 用户只描述现象，AI 负责拆解问题。
2. AI 优先基于整段对话理解上下文，而不是单条输入。
3. AI 优先小步试探，不预先下发大批命令。
4. 所有设备动作只允许 `show/display/dis`。
5. 每条命令都必须对应一个待验证假设。
6. 同会话同设备同命令只执行一次，结果复用。
7. 每轮必须给出阶段结论，而不是只给状态。
8. 快速问题优先走 deterministic fast-path，复杂问题再调用 LLM 深分析。

## 3. 用户体验目标
### 3.1 目标交互
用户输入：
```text
192.168.0.88 端口 down，昨天发现，帮我定位原因
```

AI 输出：
1. 复述当前理解
2. 给出初始假设
3. 说明接下来要执行的最小命令组
4. 执行后给出证据与本轮结论
5. 继续下一轮或直接收敛

### 3.2 不允许的交互
- 反复提示“缺少字段”
- 用户每一步都要手工填 `start/end`
- 明明已有直接证据，还继续机械地下一轮
- 相同命令被重复执行
- AI 只输出状态，不解释证据和结论

## 4. Agent 工作流
### 4.1 会话阶段
1. `Intake`
   - 解析整段对话
   - 提取设备 / 现象 / 时间线 / 方向
   - 判断是否足够启动
2. `Scoping`
   - 确定故障域：链路 / 路由 / 资源 / 时钟 / 防火墙 / 配置
   - 若时间窗缺失，AI 使用默认启动时间窗并告知用户
3. `Hypothesis`
   - 生成 2-4 个根因假设
   - 为每个假设分配置信度与验证优先级
4. `Probe`
   - 选择最小、最有信息增益的一组命令
5. `Evidence`
   - 抽取结构化证据
   - 更新假设分数
6. `Decision`
   - 输出本轮结论
   - 决定：收敛 / 下一轮 / 向用户追问
7. `Conclusion`
   - 输出最终根因、证据链、影响与建议

### 4.2 轮次内循环
每轮固定执行：
1. 生成假设
2. 生成最小命令集
3. 执行命令
4. 抽取证据
5. 更新假设
6. 判断是否收敛

说明：
- `Baseline` 是会话级动作，只做一次
- 后续轮次不应再重复基线命令，除非问题方向明确要求复核

## 5. 时间窗策略
### 5.1 Agent 原则
时间窗不是硬性拦截字段，而是 AI 的诊断上下文。

### 5.2 启动策略
- 用户明确给时间窗：直接使用
- 用户只给模糊时间：AI 解析为可用时间窗
- 用户完全未给时间：AI 使用默认启动时间窗并继续

### 5.3 默认启动时间窗
- 默认：最近 `24h`
- 后续版本按故障类型动态调整：
  - 端口 down / flap：24h
  - 重启 / crash：72h
  - 性能 / 丢包：6h~24h

### 5.4 时间收敛
AI 需要在分析中持续修正时间窗：
- 设备时间校准
- 日志时间对齐
- 告警事件发生点
- 用户补充信息

## 6. 诊断内核要求
### 6.1 诊断意图优先
AI 不直接输出厂商命令，而输出诊断意图，例如：
- `interface_summary`
- `interface_counters`
- `interface_admin_config`
- `recent_logs`
- `stp_status`
- `optic_status`
- `cpu_memory`
- `routing_neighbor`

系统负责把意图映射为不同厂商命令。

### 6.2 证据层
必须把原始命令回显转换成结构化证据，例如：
- `interface_admin_state=down`
- `interface_oper_state=down`
- `last_log_event=shutdown`
- `crc_error_delta=0`
- `stp_blocking=false`

LLM 优先基于证据推理，而不是直接消费整段 CLI。

### 6.3 假设层
每个假设至少包含：
- `title`
- `domain`
- `score/confidence`
- `evidence_for`
- `evidence_against`
- `next_intents`

### 6.4 收敛层
满足以下任一条件可收敛：
- 出现直接证据
- 假设置信度超过阈值
- 多轮后增量证据不足
- AI 判断下一步应转向其他设备/域

## 7. Fast Path / Deep Path
### 7.1 Fast Path
用于明显问题，优先 deterministic：
- admin down / shutdown
- errdisable
- 明确链路 flap
- BGP/OSPF 邻居 down
- CPU/内存高压
- 时钟偏移显著

### 7.2 Deep Path
用于复杂问题：
- 多证据冲突
- 多设备关联
- 无明显直接证据
- 需要从长文本中归纳原因

### 7.3 切换规则
- 先 Fast Path
- 未收敛时再进入 Deep Path
- Deep Path 超时则回退 deterministic 分析，不允许长时间卡死

## 8. UI 工作台要求
### 8.1 左侧：AI 对话区
- 用户输入
- AI 回复
- AI 动作卡
- 阶段结论
- 对话内追问与确认

### 8.2 右上：分析显示区
- 显示折叠详情
- 当前假设
- 证据摘要
- 计划命令
- 本轮结论

### 8.3 右下：设备终端区
- 左侧命令列表
- 右侧选中命令的回显
- 当前正在执行的命令固定显示
- 历史命令支持回看

### 8.4 输入区
只保留：
- 输入框
- 停止按钮
- 自动批准菜单
- 经验库菜单
- 模型路由菜单

## 9. 自动与手动模式
### 9.1 自动模式
AI 自动执行：
- create
- baseline
- plan
- approve
- execute
- analyze
- next_round / conclude

### 9.2 手动模式
用户通过对话内动作卡控制关键步骤。

### 9.3 统一要求
- 不在页面其他区域重复放置同一动作按钮
- 当前唯一有效动作必须由后端 `next_action` 决定

## 10. 后端状态机要求
后端统一输出 `next_action`，前端只负责展示与触发：
- `create`
- `baseline`
- `plan`
- `approve`
- `execute`
- `analyze`
- `next_round`
- `conclude`
- `wait`
- `none`

前端不得自行推断越权状态。

## 11. 可观测性要求
每轮必须有：
- 计划命令清单
- 执行结果摘要
- 结构化证据摘要
- 本轮结论
- 下一步原因
- 性能耗时

## 12. 验收标准
### 12.1 交互验收
- 用户分多次输入问题时，AI 能合并理解上下文
- 用户不给明确时间窗时，AI 仍能启动诊断
- AI 不反复输出“缺少字段”式提示

### 12.2 执行验收
- 同命令不重复执行
- baseline 每会话只做一次
- 直接证据命中后不继续无意义下一轮

### 12.3 性能验收
- Fast Path 问题在 30 秒内给出阶段结论
- Deep Path 问题分析阶段目标小于 2 分钟
- 模型不可用时自动切容灾或 deterministic fallback

## 13. 分阶段落地
### Phase 1：Agent 启动
- 对话上下文合并解析
- 时间窗不再硬拦截
- AI 默认启动时间窗
- 缺参提示改为工程师式追问

### Phase 2：Hypothesis Loop
- 轮次内以假设驱动替代大计划驱动
- 每轮只下发最小命令集
- 本轮强制输出结论

### Phase 3：Evidence Workbench
- 右侧分析区与终端区分离
- 证据与终端回显解耦
- 命令列表支持回看

### Phase 4：Adaptive Agent
- 按故障类型动态时间窗
- 多设备跳转
- 更强的经验库与案例库注入

## 14. 本轮已落实
本轮代码已落下以下 Phase 1 能力：
1. 输入解析改为优先使用“整段对话上下文”
2. 用户未明确提供时间窗时，AI 可默认按最近 24 小时启动
3. `POST /api/netdiag/sessions` 后端已支持自动补全默认时间窗
4. 缺少时间窗时，对话中提供 `AI 自行判断时间窗` 按钮
5. 支持 `前天 / 昨天 / 今天` 这类单独时间补充
6. `plan` 已增加最小验证预算，首轮默认压缩为最小探测命令集
7. `analyze` 已强制追加标准化 `[Round Conclusion]` 结论块，便于前端稳定提取
8. SOP 选步已改为优先围绕头号假设挑选最小意图集，而不是默认并行扩展多个假设
9. 命中明确直接证据时，支持快速收敛，不再继续机械进入下一轮
10. 首选模型不可用时，可自动切容灾或 deterministic fallback
11. 对话中的命令清单/执行明细/分析详情会自动投送到右侧分析显示区，聊天区仅保留摘要卡
12. `next_round` 已开始继承上轮收敛理由，作为“目标验证提示”进入下一轮，而不是纯空转切轮
13. 收敛判定已统一：当前后端 `next_action` 或 `stop_decision.next_action` 指向 `conclude_with_verification` 时，前后端都会直接走收敛路径
14. 终端区已支持跨轮次历史命令持续显示，并记住当前会话下的命令选中项
15. 终端区已支持 `全部轮次 / 当前轮次 / 指定轮次` 过滤，并在全部轮次视图下按轮次显示分组头
16. `next_round` 的目标验证提示已做去重，避免同一提示重复堆入 follow-up
17. 终端区已支持快速检索（命令/设备/状态）
18. `next_round` 的目标验证提示已压缩为更短的单步验证指令
19. 终端区检索结果已支持可见高亮，便于快速定位命中项
20. 主路径中的高频缺参提示已改成工程师式追问，不再直接报“缺字段”
21. `next_round` 的目标验证提示已不再只停留在 UI，而会作为隐式 follow-up 注入下一轮 `plan`
22. `next_round` 的目标验证提示已升级为结构化 `target_probe`，会进入 `plan` API、写入 round，并在刷新后重新恢复
23. `target_probe` 已可携带 `preferred_intents / expected_signals`，直接参与下一轮 SOP 选步
24. `expected_signals` 已进入分析层，输出命中/未命中/覆盖率并写入 `evidence_overview`
25. `expected_signals` 已开始影响 `stop_decision`：全命中可推动收敛，部分命中可提升置信度
26. 右侧分析显示区已固定显示 `target_probe / expected_signal_review / 当前判定`
27. 当前验证目标已可驱动终端区相关命令高亮
28. 右侧分析区已支持“一键跳转到相关命令/结果”，可直接把终端焦点带到当前验证目标最相关的命令和回显
29. `next_round` 已开始利用 `unmatched expected_signals` 反推 `preferred_intents`，优先补齐未验证证据
30. 前端已把 `next_round` 归一为用户侧“继续验证”语义，顶部状态与历史会话列表也改成友好文案，不再直接暴露 `need_next_round`
31. 后端 `session/next_action` 已补 `ui_status / ui_action` 元数据，前端可直接消费友好动作/状态，不需要每个组件各自翻译内部字段
32. 下一枪目标已开始由后端分析阶段直接生成并持久化到 `evidence_overview.next_target_probe`，前端刷新/恢复时优先使用服务端目标，不再只靠前端推断
33. `session / next_action` 已直接携带 `next_target_probe`，前端“继续验证”与恢复会话优先消费服务端动作输入，不再主导拼装下一枪目标
34. 统一验证任务对象已开始落地：分析阶段会生成 `validation_task`，把当前验证目标/下一枪目标/预期信号评审/未覆盖目标/收敛动作收敛到单一结构
35. `validation_task` 已进入 `plan` 主链路：继续验证时前端优先把统一任务对象发回后端，后端再从其中提取下一枪 `target_probe`
36. `validation_task` 已开始接管会话恢复与工作台渲染主源：后端优先从 `validation_task.next_probe` 恢复下一枪目标，前端分析区/终端区/继续验证入口统一先读 `validation_task`
37. 后端 `next_action.action` 已切到 `continue_probe` 主语义，并保留 `raw_action=next_round` 兼容层，动作状态机开始脱离“大轮次”命名
38. `continue_probe` 已进入 `plan_round` 工作流许可链；同时 `session dump / next_action` 的下一枪 `target_probe + validation_task` 已统一共用同一条 payload 生成链
39. 终端区已开始按 `validation_task` 自动做验证焦点排序：未命中信号/优先意图/当前验证目标命中的命令会优先排前，默认选中也会优先落到这些高价值命令
40. 前端分析区/终端区已进一步减少对旧 `target_probe / next_target_probe` 的直接读取，统一优先走 `validation_task`；当验证任务要求 `related_commands` 且存在相关命令时，终端区会默认自动切到“相关视图”
41. 服务端 `session dump` 会为 `rounds[*].evidence_overview` 注入标准化 `validation_task / next_target_probe`，前端恢复历史 round 时不再需要临时从旧字段拼装
42. 前端 `_resolveValidationTask(...)` 已继续收紧为 `validation_task` 主源：当 round/session 已带标准化任务对象时，不再优先回读旧 `target_probe / next_target_probe / expected_signal_review`
43. 顶部步骤条与“继续验证”同步逻辑已开始优先消费 `ui_status`，展示层继续脱离原始 `need_next_round`
44. 终端区已新增验证任务聚类元数据：`相关视图` 下会按“未命中信号 / 优先意图 / 已命中信号 / 其他”分组与排序，当前验证目标对命令列表的收束更稳定
45. 前端 `_buildNextTargetProbe(...)` 已改为优先基于 `validation_task` 推导下一枪目标，不再优先直读旧 `next_target_probe / expected_signal_review`
46. 终端区 `相关视图` 已开始在默认场景下自动收束到最高价值簇，优先展示当前验证任务最关键的未命中/优先意图命令
47. 前端已新增 `_sessionReadyForNextProbe(...)`，顶部状态、继续验证目标同步、按钮启用条件共用同一套“待继续验证”判断，继续削减前端状态双轨
48. 终端区在自动收束场景下已开始固定“最优回放焦点”，不再优先恢复旧选中项，而是优先锁定当前最高价值命令
49. 后端 `_session_continue_probe_payload(...)` 已去掉冗余 fallback，下一枪 payload 继续收向统一 `validation_task`
50. 后端 `continue_probe` 已成为 canonical action，`next_action.raw_action` 直接返回 `continue_probe`，旧 `next_round` 仅以 `legacy_action` 兼容保留
51. `plan` 期 round 已直接持久化标准化 `validation_task / next_target_probe`，且 round 相关 API 响应统一走 `_round_response_payload(...)`，减少计划期/分析期结构断层
52. 终端区已开始按验证任务自动选择最佳过滤范围：未手工锁定时会在 `related/current/all` 之间自动切到最合适视图；手工切换后则按会话记住并尊重该选择
53. 前端 `_resolveValidationTask(...)` 主路径已不再直接回读 `session.next_target_probe / next_action.target_probe`，统一优先消费标准化 `validation_task`
54. 前端 `_resolveValidationTask(...)` 已进一步补齐标准化任务对象缺口：当已有 `validation_task` 但字段不完整时，会优先从 round 的 `target_probe / next_target_probe / focus_review / stop_decision` 补齐
55. 终端区在自动范围场景下已开始持续保持最佳焦点：未手工锁定且存在验证焦点时，会忽略旧选中项并优先固定到当前最佳命令
56. 后端 `ui_status` 已开始按 workflow action 推导：当下一步为 `continue_probe` 时，会稳定返回 `ready_for_next_probe`，不再只靠 raw `status` 映射
57. 后端已开始在可继续验证的直接路径上真实写入 raw `ready_for_next_probe`，包括 Baseline 成功/复用、Resume、Execute 完成、Analyze 完成，以及部分 stale recovery
58. 终端区已取消额外 `expected_signal_review` 传递，统一从 `validation_task` 派生信号评审，继续减少前端双轨状态
59. 项目内部测试与回归口径已开始切到 raw `ready_for_next_probe`，进一步减少 `need_next_round` 作为主状态的使用面
60. 后端 round 级 `validation_task` 标准化已可在持久化对象字段不完整时，自动用 `target_probe / next_target_probe / focus_review / stop_decision / expected_signal_review` 补齐，减少前端主链重复补洞
61. 前端 `_resolveValidationTask(...)` 已进一步收紧：当 round/session 已带标准化 `validation_task` 时直接使用该对象，旧 `target_probe / next_target_probe / expected_signal_review` 仅保留历史兼容兜底
62. 终端区在自动范围切换场景下已开始持续保持最佳焦点，旧选中项不会再把当前最高价值命令抢回
63. 终端区未手工锁定过滤器时，已开始持续记住上一轮自动选出的最佳范围；只要该范围仍有效就继续沿用，减少 `related/current/all` 之间的无意义抖动
64. 继续验证发起计划时，前端若已有标准化 `validation_task`，不再强制同时提交独立 `target_probe`；后端优先从统一任务对象提取下一枪目标
65. `plan_round / analyze_round` 当前落盘的 `validation_task` 已统一经过上下文补齐，减少后续恢复/继续验证再从旧字段二次拼装
30. 终端区已支持 `相关视图` 过滤，可直接聚焦当前验证目标相关命令
31. `target_probe` 已补 `hypothesis_id / stop_if_matched` 字段，开始向统一验证任务对象收敛
32. `target_probe` 已补 `expected_evidence / stop_reason / preferred_scope` 字段，形成验证任务对象雏形
33. 未命中信号已开始进入 SOP 步骤和 PlannedCommand 的 `expected_signal`
34. 用户侧 `next_round` 展示已改为“下一枪验证 / Next Probe”，开始弱化“大轮次”心智
35. `stop_if_matched=true` 的验证任务已会压成单枪预算，进一步贴近“打一枪验证”的代理循环
36. `expected_evidence / stop_reason` 已进入分析与收敛链路，命中后会按验证任务对象定义的动作收敛
37. `preferred_scope=related_commands` 已可驱动终端默认聚焦到“相关视图”
38. `相关视图` 已可按验证意图/期望证据分组，终端不再只是平铺相关命令
39. `相关视图` 已可按“未命中信号 / 已命中信号 / 意图”分层，进一步贴近验证闭环

## 15. 暂未完成
1. 轮次内仍以 `plan -> approve -> execute -> analyze` 为主，尚未完全切换为“逐条验证式”的最小假设循环
2. 右侧分析区/终端区仍未彻底按 Agent Workbench 重构
3. 缺参提示仍有少量字段化文案，需继续改成工程师式追问
4. 动态启动时间窗策略尚未按问题类型自适应
5. 证据层仍未完全统一为跨厂商结构化证据对象
6. Fast Path / Deep Path 仍未完成完整分流与耗时归因
7. 右侧工作台虽已具备“聊天摘要 + 右侧详情”的分工，但终端区与分析区的深度联动仍可继续优化
8. 终端区的分组视图、按轮次过滤与更强的回放检索仍未完成
9. 目标验证提示虽然已去重，但还未与结构化证据层完全打通
10. 终端区仍缺更强的关键词高亮与按证据/状态聚类
11. 仍有少量边缘路径提示文案可继续向工程师式追问收敛
12. `target_probe` 已打通主路径、补齐更多任务字段并可驱动选步，但还未与完整结构化证据层沉淀成统一“验证任务对象”
13. `expected_signals` 已能影响 `stop_decision`，但还未进入完整统一证据判定模型
14. 右侧分析显示区虽已固定显示结构化摘要，但与终端区仍未形成“按目标自动聚类”的更深联动
15. 终端区虽已能高亮相关命令、支持一键跳转和 `相关视图` 过滤，但还缺更深的目标驱动聚类
66. `plan_round` 已进一步收向统一任务对象：后端优先从 `validation_task` 提取 `target_probe`，独立 `target_probe` 退回兼容输入兜底
67. 终端区在自动收束场景下会持续记住上一轮自动选中的最佳相关命令簇，只要该簇仍有效就继续沿用，减少相关命令分组跳动
68. 终端区在自动聚焦场景下会持续记住上一轮自动选中的最佳命令，只要该命令仍有效就继续沿用，刷新和继续验证后不再轻易跳焦
69. 边缘时间窗追问已继续改成工程师式提示：AI 会先按安全默认时间窗启动，再根据设备证据逐步缩小范围，不再直出内部字段式表达
70. 前端 `_resolveValidationTask(...)` 已切到只消费标准化 `validation_task`，主链不再从旧 `target_probe / next_target_probe / expected_signal_review` 现拼任务对象
71. 后端 `期望信号评审 / 收敛判定修正 / 下一枪目标推导` 已开始优先消费统一 `validation_task`，减少 helper 内部对旧 `target_probe / expected_signal_review` 的直接依赖
72. 前端下一枪目标 `source` 已统一改为 `continue_probe`，继续验证计划请求仅在缺少 `validation_task` 时才附带兼容 `target_probe`
73. 前端动作卡、自动推进、步骤条已不再显式依赖 `next_round / need_next_round`，统一按 `continue_probe / ready_for_next_probe` 主口径工作，旧别名只保留在归一化兼容入口
74. `session_manager` 在载入持久化会话和 `set_status()` 时，会把旧 `need_next_round` 统一归一成 raw `ready_for_next_probe`，旧主状态不再重新进入内存主链
75. `need_next_round` 已从工程主状态集合中移除，项目内部主状态统一为 raw `ready_for_next_probe`，旧状态只保留在兼容归一化层
76. 前端 `_buildNextTargetProbe(...)` 已切到仅消费标准化 `validation_task`，不再从旧 round 字段本地二次推断下一枪目标
77. 前端继续验证提交流已完全改为只提交 `validation_task`，正常主链不再主动发送兼容 `target_probe`
