# 网络设备批量巡检工具 - 新手帮助文档

本项目用于通过 SSH 批量登录网络设备并执行巡检命令，支持：
- Huawei
- Cisco
- Arista

并支持两种执行方式：
- 直接命令（如 `display version` / `show version`）
- 意图命令（如 `@cpu_usage`），由映射文件自动转换为不同厂商命令

---

## 1. 你需要准备哪些文件

### 必需文件
1. `huawei_switch_manager.py`
- 主程序（虽然文件名叫 huawei，但当前已支持多厂商）

2. `devices.txt`
- 设备列表，每行一个 IP 或主机名

3. `command_map.yaml`
- 设备识别规则 + 意图到命令映射

### 常用可选文件
4. `intents.txt`
- 巡检意图列表（每行一个，如 `@version`）

5. `commands.txt`
- 如果你想直接下发原始命令，可以用这个文件（每行一个命令）

---

## 2. 快速上手（推荐）

### 第一步：准备设备列表
`devices.txt` 示例：

```txt
192.168.0.88
10.10.10.1
core-sw-01
```

### 第二步：准备巡检意图
`intents.txt` 示例：

```txt
@version
@cpu_usage
@memory_usage
@interface_brief
@route_summary
@running_config
```

### 第三步：运行程序
在当前目录执行：

```bash
python3 huawei_switch_manager.py
```

程序会依次询问：
1. SSH 用户名
2. SSH 密码
3. 命令映射文件（回车默认 `command_map.yaml`）
4. 设备输入（如 `devices.txt`）
5. 命令或意图输入（如 `intents.txt`，也可直接输入并用逗号/分号/换行分隔）

---

## 3. 程序执行逻辑（核心理解）

每台设备的处理流程：
1. 建立 SSH 连接（交互式 shell）
2. 自动尝试关闭分页（避免长输出被截断）
3. 通过 `display version` / `show version` 识别设备画像（profile）
4. 对每个执行项进行处理：
- 若是原始命令：直接执行
- 若是 `@intent`：按画像从 `command_map.yaml` 找候选命令并执行
5. 候选命令失败会自动回退到下一条候选命令
6. 输出每个执行项的结果并统计成功/失败

---

## 4. 意图映射的回退顺序

当你输入 `@intent` 时，命令选择顺序是：
1. 当前识别到的 profile（例如 `cisco.iosxe.cat9k.17`）
2. 厂商默认 profile（例如 `cisco.default`）
3. 全局兜底 profile（`generic.unknown`）

这意味着：
- 即使软件版本无法精确识别，只要识别到厂商（比如 Cisco），也会先用该厂商默认命令
- 仍失败时才会使用通用兜底命令

---

## 5. 文件格式规范

### `devices.txt`
- 每行一个设备地址
- 支持 IPv4 / IPv6 / 主机名
- `#` 开头行会被忽略（注释）

### `intents.txt` / `commands.txt`
- 每行一个执行项
- `@` 开头表示意图，例如 `@bgp_summary`
- 非 `@` 行按原始命令执行
- `#` 开头行会被忽略（注释）
- 交互输入时也支持使用逗号 `,`、分号 `;`、换行分隔多个执行项

---

## 6. 常见问题

### Q1: 某个命令显示失败，但下一条尝试成功
正常。表示该设备不支持第一候选命令，程序已自动回退到下一候选。

### Q2: 识别成 `generic.unknown`
说明版本输出未匹配到任何厂商/型号规则。可在 `command_map.yaml` 增加该型号识别规则。

### Q3: 为什么密码有时会显示告警（echo）？
如果你用管道自动传入密码（非交互终端），`getpass` 可能提示无法隐藏输入。这是终端行为，不影响脚本逻辑。

### Q4: 某些意图在某型号上没有结果
可能该厂商/版本命令不一致。请在对应 profile 的该意图下补充候选命令。

---

## 7. 如何扩展新设备型号

在 `command_map.yaml` 的 `profiles` 下新增条目：
1. 定义唯一 `id`
2. `vendor` 填厂商
3. `match` 写版本匹配规则（regex）
4. `commands` 填各意图候选命令

建议原则：
- 精确型号 profile 放前面
- `xxx.default` 放后面
- `generic.unknown` 永远放最后兜底

---

## 8. 建议的生产使用方式

1. 先用小规模设备测试（1-2 台）
2. 首次上线只跑只读意图（不含配置改动）
3. 确认映射命令无误后再批量执行
4. 对关键命令保留人工复核

---

## 9. 当前项目文件建议布局

```txt
huawei_switch_manager.py
command_map.yaml
devices.txt
intents.txt
commands.txt
HELP.md
```

---

## 10. 一条命令总结

日常巡检通常只要：
- 设备：`devices.txt`
- 意图：`intents.txt`
- 映射：`command_map.yaml`

然后运行：

```bash
python3 huawei_switch_manager.py
```

---

## 11. Web 页面执行方式（新增）

如果你想用页面方式执行，可使用 `web_runner.py`：

```bash
cd healthcheck
python3 web_runner.py
```

浏览器打开 `http://127.0.0.1:8080`，然后：
1. 输入 SSH 用户名和密码
2. 输入设备地址（每行一个）
3. 选择检查项（来自 `intents.txt`）
4. 可选上传 `command_map.yaml`（不上传默认使用同目录 `command_map.yaml`）
5. 点击“执行 Python 巡检脚本”

页面会调用同目录下的 `healthcheck.py`，并在页面展示执行输出。
点击执行后会立即跳转到“任务状态页”，状态与日志会自动轮询刷新（执行中/完成/失败）。
页面支持勾选 Debug 模式：开启后显示完整原始日志，关闭后显示精简日志（更干净）。
页面也支持设置执行模式（auto/serial/parallel）、并发 workers、连接重试次数。
检查项按分类展示（设备软件层/硬件层/协议层面/端口层面/更多分类），每个分类都支持“全选”。
支持“自定义命令”输入（多行），会在勾选检查项之后按从上到下顺序执行。
设备地址支持手动输入，也支持导入设备文件；导入后会直接刷新到“设备地址”文本框并可继续编辑。
同时页面顶部会展示：
- 任务成功后在任务状态页显示“本次 JSON/CSV 报告”下载按钮

GPT 分析功能：
- 在任务状态页支持两种分析方式：
  - OpenAI API
  - LM Studio（本地 OpenAI 兼容接口）
- 默认 LM Studio 配置：
  - 地址：`http://192.168.0.99:1234`
  - 模型：`qwen/qwen3-coder-30b`
- 可保存 LLM 配置（保存后下次可直接使用）
- API Key 保存在 `healthcheck/gpt_config.json`（本地明文），请注意权限管理
- 内置默认网络诊断提示词模板
- 支持导入 `.txt` 提示词文件并保存为自定义模板
- 支持“连接测试”按钮（先验证 OpenAI/LM Studio 连通性，再进行分析）
- 点击“GPT 分析本次结果”会把任务日志和结构化报告提交给 GPT 分析
- GPT 分析结果会显示在任务状态页下方

报告说明：
- `attempt_output_preview`：压缩后预览（便于快速浏览）
- `attempt_output_full`：完整原始命令输出（含换行）
