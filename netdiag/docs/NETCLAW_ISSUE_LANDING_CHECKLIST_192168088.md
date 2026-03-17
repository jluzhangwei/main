# NetClaw 问题落地清单与 192.168.0.88 回归结果

## 1. 范围

本文件把此前用户反复提出的问题整理成可执行验收项，并在 `192.168.0.88` 上做真实回归。

本轮真实设备范围：

- 设备：`192.168.0.88`
- 登录：直连 SSH
- 场景：`2026-03-10 20:30` 之后 `Ethernet1/0/6` 告警
- 预期根因：接口被 `shutdown`，最终应收敛到管理性关闭 / admin-down
- 命令约束：只允许 `show` / `display` / `dis`

本轮输出物：

- 设备回归报告：`output/regression/20260312T040535Z/issue_regression_report.json`
- 回归脚本：`scripts/netdiag_issue_regression.py`

## 2. 本轮结论

真实设备/API 回归共 `6` 项，结果：

- `PASS=6`
- `FAIL=0`

真实设备 3 轮稳定性摘要：

- `plan` 平均 `0.526s`，最大 `0.543s`
- `execute` 平均 `4.147s`，最大 `4.194s`
- `analyze` 平均 `0.639s`，最大 `0.657s`
- 单轮总耗时平均 `9.938s`，最大 `10.169s`

结论：

- 之前“`plan/analyze` 卡住很久”的关键路径，在当前 `192.168.0.88` 场景下未复现。
- 之前“Baseline 后仍重复下发 `display clock/version/cpu-usage`”的问题，在本轮已修复并通过真实设备验证。
- 当前场景中，AI 已能在第 1 轮收敛到 `Ethernet1/0/6 administratively down / shutdown`，并把 `next_action` 正确置为 `conclude`，不会再错误继续下一轮。

## 3. 实际已完成

以下项目已被本轮真实设备/API 回归直接证明：

- 模型路由守卫：首选 ChatGPT 不可用时，会自动切到 DeepSeek，不再卡死在 `planning`
- 自然语言解析：支持全角 IP、支持“昨天到今天”这类模糊时间窗
- `baseline` 同会话只采一次，重复调用会直接复用
- baseline 之后的首轮 `plan` 不再重复插入 `display clock` / `display version` / `display cpu-usage`
- 同一轮计划命令已去重；执行结果可通过 `session outputs` 回看
- `analysis_result` 不为空，且本场景分析耗时远低于 2 分钟
- 命中 shutdown/admin-down 直接证据后，流程会转到 `conclude`，不会错误再开下一轮
- `stop -> blocked -> resume -> history session` 链路已打通

## 4. 尚未完成 / 尚未闭环验证

以下项目没有被本轮 `192.168.0.88` 设备回归直接证明，因此不计入“已完成”：

- 纯前端视觉与交互细节：
  - 左右工作区比例、拖拽手感、分割线观感
  - 右上显示区中折叠内容展示是否符合预期
  - 聊天区按钮弹出时机、按钮尺寸与样式细节
  - 历史会话页签的人工操作体验
  - 终端样式是否完全符合“左侧命令列表 + 右侧回显”的交互预期
- 页面级人工验收：
  - 切页/刷新后的浏览器端自动滚动、焦点、悬浮提示是否完全符合预期
  - 各页面图标、间距、空白、折叠动画等视觉项
- 多设备覆盖面：
  - Cisco / Arista / Palo Alto 在同一套回归清单下的真实设备验证
  - SMC 跳板场景下的同等回归
- 长时稳定性：
  - 本轮只做了 3 轮真实闭环，不等于重新完成 10 小时长跑
  - 若要确认长时间无卡死，需要继续跑 `scripts/netdiag_longrun_test.py`

## 5. 本轮修复点

本轮发现并修复 1 个仍然存在的问题：

- 问题：通用“时间/证据链”表达会误触发 baseline 类命令复查，导致首轮计划仍包含 `display clock` / `display version` / `display cpu-usage`
- 根因：`_need_baseline_recheck()` 判断过宽，把普通“时间”语义误判成“时钟问题”
- 修复：只在明确命中 `clock / timezone / NTP / 时钟 / 时区 / 版本 / CPU / 内存 / 资源` 等基线类诉求时才允许 baseline 复查；不再因为“时间校准后的证据链”而回退
- 代码：
  - `app/routers/netdiag.py`
  - `tests/test_progressive_plan_baseline.py`

## 6. 设备/API 验收清单

| ID | 用户历史问题 | 验收标准 | 本轮结果 | 证据 |
|---|---|---|---|---|
| R-01 | 选了 ChatGPT，但新流程/新回合卡在 `planning` | 首选 ChatGPT 不可用时，运行时必须自动切到可用容灾模型 | PASS | `route/check` 返回 `switched_to_failover=true`，运行时主模型为 `deepseek` |
| R-02 | 用户自然语言输入设备/时间，系统不理解 | `192。168。0。88` 和“昨天到今天”必须解析出 `device_ip/fault_start/fault_end` | PASS | `intent/parse` 返回 `device_ip=192.168.0.88`，时间窗完整 |
| R-03 | `Baseline` 不该每轮重跑 | 同一会话第二次调用 `baseline_collect` 必须返回 `baseline_reused=true` | PASS | 会话 `21929dc0d049` 第二次 baseline 直接复用 |
| R-04 | Baseline 后首轮计划仍出现 `display clock/version/cpu-usage` | 非时钟专项排障时，首轮计划不得重下 baseline 类命令 | PASS | 3 轮真实回归计划命令都只有 `display logbuffer` 和 `display interface brief` |
| R-05 | 计划/执行会重复命令，历史回显里还会丢 | 同一轮命令唯一；执行后 `session outputs` 必须能回看命令和回显 | PASS | 3 轮中 `planned_commands == output_commands == ['display logbuffer', 'display interface brief']` |
| R-06 | `analysis_result` 为空，或者 AI 分析很慢 | `analysis_result` 非空，且 `analyze <= 120s` | PASS | 3 轮 `analyze` 分别为 `0.654s/0.606s/0.657s` |
| R-07 | 明明已有直接证据，却还继续下一轮 | `stop_decision.recommend_conclude=true` 后，`next_action` 必须是 `conclude` | PASS | 3 轮都命中 `next_action_after_analyze.action == conclude` |
| R-08 | Stop 不彻底，新会话/历史会话状态混乱 | `stop` 后 workflow 被阻断；`resume` 后恢复到正确下一步；新会话仍保留历史会话 | PASS | `plan_round` 在 stop 后返回 `409 paused by emergency stop`；`resume -> next_action=plan`；历史会话列表保留旧会话 |

## 7. 真实设备关键证据摘录

会话：`21929dc0d049`

计划命令：

- `display logbuffer`
- `display interface brief`

分析核心结论：

- 命中直接证据 `huawei_interface_admin_down`
- 命中直接证据 `huawei_interface_shutdown_event`
- 命中直接证据 `interface_admin_down_present`
- 根因判定：`Ethernet1/0/6` 被管理性关闭（admin-down / shutdown）
- `stop_decision.recommend_conclude=true`
- `next_action=conclude`

## 8. 相关单测补充验证

为避免这次修完下次再回退，本轮还补跑了以下单测集：

```bash
/Users/zhangwei/python/.venv/bin/python -m pytest -q \
  tests/test_progressive_plan_baseline.py \
  tests/test_llm_route_selector.py \
  tests/test_intent_parse_endpoint.py \
  tests/test_analyze_fastpath.py
```

结果：

- `26 passed in 2.59s`

覆盖点：

- baseline 后 progressive plan 过滤
- LLM route failover / 可用性检查
- 自然语言解析（全角 IP、昨天到今天、方向去重）
- fast-path analyze / 直接证据收敛

## 9. 纯前端问题说明

此前还提过一批纯 UI/交互问题，例如：

- 左右工作区布局
- 对话区按钮按需弹出
- 历史会话入口位置
- 停止按钮位置
- 右侧显示区/终端区样式
- logo 切换、标题切换

这些不适合用 `192.168.0.88` 做设备级验证，只能做浏览器人工点验或前端代码核查。

本轮未把这些 UI 视觉项混入“真实设备已通过”的结论里；设备回归只覆盖可被 API/设备证据直接证明的路径。

## 10. 复跑命令

```bash
cd /Users/zhangwei/python/netdiag
/Users/zhangwei/python/.venv/bin/python scripts/netdiag_issue_regression.py \
  --base-url http://127.0.0.1:8001 \
  --device-ip 192.168.0.88 \
  --username zhangwei \
  --password '***' \
  --iterations 3
```
