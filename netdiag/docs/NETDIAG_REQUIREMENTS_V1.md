# NetDiag 可用性提升需求文档（V1 落地版）

## 1. 文档目标
在当前 `netdiag` 基础上，提升“日常可用性、诊断连续性、历史可追溯性”。  
本版本只定义可落地需求，不改变核心前提：
- 只读命令（`show/display/dis`）
- 人工审批执行
- 时间窗驱动诊断
- 多厂商兼容

## 2. 当前系统能力（现状分析）
- 已有：会话/轮次/SOP 假设评分、已知问题库、命令学习库、SMC/SSH 执行、时间校准、Zabbix 历史查询。
- 已有：结构化证据解析（命令输出 -> domain signal）。
- 已有：LLM 超时 deterministic fallback。
- 缺口：缺少“跨会话历史状态库”“配置历史库”“历史变更与故障关联”。
- 缺口：Zabbix 已可查，但尚未形成“可复用的设备长期画像”。

## 3. 需求总览
### 3.1 设备状态库（Device State Library）
#### 目标
按设备持续积累历史状态，支持“本次异常 vs 历史基线”对比。

#### 需求
- 记录粒度：`device_id + timestamp + domain + key + value + source(round/baseline/zabbix)`。
- 状态域：`link/routing/resource/firewall/clock`。
- 提供查询：
  - 按设备/时间窗拉取状态趋势
  - 按 domain 查看异常峰值
  - 近 N 次诊断变化摘要
- 在 analyze 阶段注入：
  - 当前值相对历史中位数/95分位偏移
  - 偏移是否显著（自动生成 signal）

#### 对现有代码改造点
- 新增 `app/diagnosis/state_store.py`
- `execute/analyze` 完成后写入状态点
- 新增 API：
  - `GET /api/netdiag/state/{device_id}`
  - `POST /api/netdiag/state/query`

#### 验收标准
- 同设备连续 7 天有状态点沉淀
- analyze 输出可见 `[Historical Baseline Compare]`
- 至少 1 个 domain 支持“偏移自动加权”

### 3.2 历史配置库（Config History Library）
#### 目标
支持“故障前后配置差异”回顾，提升根因定位速度。

#### 需求
- 采集只读配置快照（按厂商选择命令）：
  - Cisco/Arista: `show running-config`（或分页片段）
  - Huawei: `display current-configuration`
  - Palo Alto: `show config running`
- 存储：`device_id + collected_at + hash + text_path + parser_meta`
- 提供配置 diff：
  - 任选两个快照对比
  - 自动提取关键差异（路由策略、接口、ACL/安全策略、NTP）
- analyze 可选附带“故障窗前后配置差异摘要”。

#### 对现有代码改造点
- 新增 `app/diagnosis/config_store.py`
- 新增 API：
  - `POST /api/netdiag/config/snapshot`
  - `POST /api/netdiag/config/diff`
  - `GET /api/netdiag/config/{device_id}/history`
- UI：`/netdiag` 增加 Config Snapshot/Compare 区块

#### 验收标准
- 支持每设备至少 50 份快照
- diff 在 10 秒内返回（200KB 配置文本）
- 输出关键变更条目，不低于 80%可读性（人工验收）

### 3.3 故障案例库（Case Library）
#### 目标
把成功排障过程沉淀为“可复用 SOP”。

#### 需求
- 会话结案时可一键保存为案例：
  - 现象、时间窗、证据链、最终根因、处置动作、验证动作
- 检索维度：厂商、域、关键词、版本、信号模式
- planning 阶段可作为候选先验（低于 known issue 权重）

#### 改造点
- 新增 `app/diagnosis/case_store.py`
- 新增 API：
  - `POST /api/netdiag/cases/from_session/{session_id}`
  - `POST /api/netdiag/cases/search`

### 3.4 Zabbix 深化（从查询到诊断融合）
#### 目标
把 Zabbix 从“历史查询工具”升级为“诊断证据源”。

#### 需求
- 支持 item 模板映射（按厂商/域）：
  - CPU、内存、接口丢包、接口错误、BGP 邻居状态
- 支持自动时间对齐：
  - 默认使用故障窗 `start_at/end_at`
- 自动生成信号并参与 SOP 打分（已部分具备，需完善规则与阈值配置）

#### 改造点
- 新增 `state/netdiag_zabbix_item_map.json`
- 新增阈值配置 API：`POST /api/netdiag/zabbix/rules`

## 4. 非功能需求
- 安全：
  - 凭据脱敏、最小权限、审计日志
  - Zabbix token 与设备口令分离存储
- 性能：
  - 历史查询分页/限流
  - 大时间窗自动 trend 化
- 可靠性：
  - 外部依赖失败（Zabbix/LLM）不阻断主诊断流程

## 5. 里程碑建议
- M1（1-2周）：设备状态库 + 基础查询 + analyze 注入
- M2（1-2周）：配置快照与 diff
- M3（1周）：案例库 + planning 先验
- M4（持续）：Zabbix item 映射与阈值策略优化

## 6. 本版本完成定义（DoD）
- 新增模块、API、UI 均有单测
- 至少 2 台真实设备回归通过
- README 与架构文档同步更新
