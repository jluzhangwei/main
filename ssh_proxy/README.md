# SSH 透明代理使用说明

`ssh-proxy` 是一个本地透明 PTY 代理。它不要求修改你们已有的登录命令，而是把登录命令作为子进程接管：

```bash
./ssh-proxy -- <你们自己的登录命令和参数>
```

用户看到的仍然是原来的终端交互；代理在中间完成三件事：

- 登录后拦截交互命令，在发送到远端前做安全策略判断。
- 危险命令直接在本地阻断，不发送到远端。
- 记录会话元数据、命令决策和终端回放，便于事后审计。

## 目录

- [适用场景](#适用场景)
- [组件总览](#组件总览)
- [快速开始](#快速开始)
- [使用方式](#使用方式)
- [命令过滤规则](#命令过滤规则)
- [审计查看方式](#审计查看方式)
- [大模型设备故障诊断接入](#大模型设备故障诊断接入)
- [沙盒验证](#沙盒验证)
- [本地烟测](#本地烟测)
- [用户体验说明](#用户体验说明)
- [常见问题](#常见问题)
- [生产化建议](#生产化建议)

## 适用场景

适合第一版覆盖这些场景：

- 网络设备 CLI：Huawei / Cisco / Arista / Palo Alto 等只读巡检或排障。
- 内部自定义登录命令：例如 SMC、PAM、跳板命令、包装过的登录工具。
- 用户需要尽量保持原始登录体验，但公司需要危险命令阻断和审计。

不适合第一版强管控这些场景：

- `scp` / `sftp` / `rsync` 文件传输协议内容解析。
- `vim` / `top` / `less` 等全屏 raw terminal 程序里的细粒度命令识别。
- 网络层无感劫持 22 端口。SSH 已加密，网络层无法可靠过滤远端命令。

## 组件总览

当前提供三个可执行入口：

```text
ssh-proxy         # 透明代理入口，接管自定义登录命令
ssh-proxy-audit   # 审计查询入口，查看会话、命令、阻断和回放
ssh-proxy-policy  # 策略查看入口，查看后台规则并离线检查命令
```

典型流程：

```text
用户 / 大模型
  -> ssh-proxy
  -> 你们已有的登录命令，例如 smc / pam / ssh
  -> 设备 CLI
```

事后查询：

```text
ssh-proxy-audit list
ssh-proxy-audit commands
ssh-proxy-audit blocked
ssh-proxy-audit replay
```

策略检查：

```text
ssh-proxy-policy list
ssh-proxy-policy check "reload"
```

## 快速开始

在仓库根目录运行：

```bash
cd /Users/zhangwei/python
./ssh-proxy -- smc pam nd ssh 10.1.1.1
```

也可以接任意内部登录命令：

```bash
./ssh-proxy -- smc server toc jump01
./ssh-proxy -- internal-login --device 10.1.1.1 --user zhangwei
./ssh-proxy -- ssh user@10.1.1.1
```

`--` 后面的所有内容都会被当作原始登录命令，代理不会重写它。

## 使用方式

### 1. 直接使用代理前缀

推荐先用这种方式灰度：

```bash
./ssh-proxy -- <原来的登录命令>
```

例如原来用户执行：

```bash
smc pam nd ssh 10.1.1.1
```

现在改为：

```bash
./ssh-proxy -- smc pam nd ssh 10.1.1.1
```

### 2. 指定审计目录

默认审计目录：

```bash
~/.ssh_proxy/audit/<session_id>/
```

指定目录：

```bash
./ssh-proxy --audit-dir /tmp/ssh-proxy-audit -- smc pam nd ssh 10.1.1.1
```

也可以用环境变量：

```bash
export SSH_PROXY_AUDIT_DIR=/data/ssh-proxy-audit
./ssh-proxy -- smc pam nd ssh 10.1.1.1
```

### 3. 做成用户习惯命令

如果希望用户少打路径，可以建立软链接：

```bash
ln -s /Users/zhangwei/python/ssh-proxy /usr/local/bin/ssh-proxy
ssh-proxy -- smc pam nd ssh 10.1.1.1
```

如果公司后续要强制所有内部登录命令走代理，建议在内部登录工具外层统一包装，而不是让每个用户维护 alias。

## 命令过滤规则

代理启动登录命令后，前期登录过程保持透传，包括密码、MFA、跳板交互等。代理检测到远端 prompt 后，进入命令过滤模式。

### 查看后台实际规则

直接查看系统后台当前生效的 allow/block 规则：

```bash
./ssh-proxy-policy list
```

输出 JSON：

```bash
./ssh-proxy-policy list --json
```

离线检查某条命令是否会被放行：

```bash
./ssh-proxy-policy check "show version"
./ssh-proxy-policy check "reload"
```

按配置模式检查：

```bash
./ssh-proxy-policy check --config-mode "show version"
./ssh-proxy-policy check --config-mode "exit"
```

### 默认放行命令

默认只放行只读或退出类命令：

- `show ...`
- `display ...`
- `dis ...`
- `ping ...`
- `traceroute ...`
- `tracert ...`
- `exit`
- `quit`
- `end`
- `?`
- 安全前缀下的空格键
- 安全前缀下的 `?` 在线帮助键
- 安全前缀下的 Tab 命令补全键

示例：

```text
show version
display clock
dis interface brief
ping 10.0.0.1
show ?
show <Tab>
```

过滤模式下，代理会尽量模拟真实设备 CLI 的逐字符体验：普通字符会实时发送到设备，安全命令前缀下的空格、`?` 和 Tab 也会实时发送到设备，不需要等 Enter。这样 `display `、`display ?`、`show ?`、`show <Tab>` 会像直连设备一样立即触发设备侧响应。

保护逻辑只在明确边界点介入：空格、`?`、Tab、回车，或者明显的组合/逃逸字符。这样不会在用户正常打字过程中突然打断；但当输入准备进入危险命令上下文或准备提交时，会尝试用 Ctrl-U 清空远端当前行并阻断。

### 默认阻断命令

默认阻断配置、保存、重启、删除、shell 逃逸等高风险命令：

```text
configure terminal
conf t
config terminal
system-view
commit
write memory
save
reload
reboot
restart
delete flash:/config
del flash:/config
erase startup-config
format flash:
shutdown
shut
rm -rf /tmp/x
rm --recursive /tmp/x
rm --force /tmp/x
sudo ...
su ...
bash ...
sh ...
python ...
perl ...
ruby ...
lua ...
```

用户侧会看到类似提示：

```text
Blocked by SSH proxy policy: dangerous command is not allowed (reload) [reload]
```

被阻断的命令不会发送到远端。

### 组合命令和 paste

这些输入默认视为高风险：

```text
show version; reload
display clock && reboot
show users || delete flash:/x
show version | sh
show version `whoami`
show version $(whoami)
```

多行 paste 会逐行判定；只要其中一行危险，整体阻断。

### 空格、`?` 和 Tab 补全支持

网络设备上常见三类辅助输入：

```text
show 
show ?
show interface <Tab>
```

代理支持这三种交互，并且不需要按 Enter：

- 空格：发送到设备，用于触发设备侧行编辑或参数上下文。
- `?`：发送到设备，用于显示当前位置可用命令或参数。
- Tab：优先保持真实 Tab 透传给设备，用于触发设备侧补全。

为了避免某些 PTY/终端把 Tab 展开成一串空格，代理还会对明确安全缩写做本地补全：

```text
dis<Tab>  -> display 
sho<Tab>  -> show 
```

这种本地补全只针对 allow list 中唯一匹配的只读命令前缀；更深层的参数补全仍交给设备处理，例如 `display interface <Tab>` 会继续发送真实 Tab。

例如：

```text
show             # 空格允许即时到设备
show ?          # 允许
display ?       # 允许
dis <Tab>       # 允许
reload          # 打字过程不打断，按空格或回车时阻断
reload ?        # 阻断
system-view ?   # 阻断
show ; reload ? # 阻断
```

如果用户在已经触发过空格、`?` 或 Tab 的同一行里继续输入危险内容，代理会在边界点或回车时阻断，并尝试用 Ctrl-U 清空远端当前行，避免提交执行。

### 配置模式保护

代理如果从远端输出中识别到配置模式 prompt，会进入更严格状态。配置模式下只允许退出类命令：

```text
exit
quit
end
abort
return
```

其他命令会被阻断，避免用户继续下发配置。

### 当前后台规则清单

后台实际规则来自 `ssh_proxy/policy.py`，可以用命令查看：

```bash
./ssh-proxy-policy list
```

当前危险规则按类型分为：

```text
configure     -> configure / conf t / config terminal
system-view   -> system-view
commit        -> commit
write         -> write / save
reload        -> reload / reboot / restart
delete        -> delete / del / erase / format
shutdown      -> shutdown / shut
shell-delete  -> rm -rf / rm --recursive / rm --force
privilege     -> sudo / su
script-exec   -> bash / sh / python / perl / ruby / lua
```

组合和 shell 逃逸规则：

```text
compound-separator   -> ; / && / ||
command-substitution -> `...` / $(...)
pipe-to-shell        -> | sh / | bash / | python / | perl / | ruby
```

如果命令没有命中 allow list，也没有命中危险规则，最终会被 `default-deny` 阻断。

## 审计查看方式

推荐使用 `ssh-proxy-audit` 查看审计，而不是手工 grep JSONL。

### 查看最近会话

```bash
./ssh-proxy-audit list
```

指定审计目录：

```bash
./ssh-proxy-audit --audit-dir /tmp/ssh-proxy-audit list
```

输出示例：

```text
SESSION       STARTED                 EXIT      TARGET            COMMAND
4f2a8d9c31b0  2026-04-26T11:20:01...  0         10.1.1.1          smc pam nd ssh 10.1.1.1
```

### 查看会话摘要

默认查看最近一次会话：

```bash
./ssh-proxy-audit show
```

查看指定 session，支持前缀：

```bash
./ssh-proxy-audit show 4f2a8d9c31b0
```

### 查看命令决策

```bash
./ssh-proxy-audit commands
./ssh-proxy-audit commands 4f2a8d9c31b0
```

输出会包含：

- 时间
- `ALLOW` / `BLOCK`
- 命中规则
- 用户输入命令
- 判定原因

### 只看被阻断命令

```bash
./ssh-proxy-audit blocked
./ssh-proxy-audit blocked 4f2a8d9c31b0
```

这适合安全复盘或日报统计。

### 查看终端回放

```bash
./ssh-proxy-audit replay
./ssh-proxy-audit replay 4f2a8d9c31b0
```

如果要保留原始 JSONL：

```bash
./ssh-proxy-audit replay --json
```

### 查看原始事件

```bash
./ssh-proxy-audit raw
./ssh-proxy-audit raw --event command_blocked
```

## 审计文件结构

每次运行都会生成一个 `session_id`，目录结构如下：

```text
~/.ssh_proxy/audit/
  latest
  sessions.jsonl
  <session_id>/
    metadata.json
    events.jsonl
    replay.jsonl
```

其中：

- `latest`：最近一次 session id，供 `ssh-proxy-audit show` 默认读取。
- `sessions.jsonl`：会话索引，每个完成的会话追加一行。
- `<session_id>/metadata.json`：单次会话元数据。
- `<session_id>/events.jsonl`：结构化事件。
- `<session_id>/replay.jsonl`：终端回放。

### metadata.json

记录会话级信息：

- `session_id`
- 本地用户名
- 本机 hostname
- 代理进程 PID
- 原始登录命令
- 目标 hint
- 开始时间
- 结束时间
- 子进程退出码

手工查看：

```bash
cat ~/.ssh_proxy/audit/<session_id>/metadata.json
```

### events.jsonl

记录结构化事件：

- `session_start`
- `child_start`
- `filtering_active`
- `command`
- `command_blocked`
- `terminal_resize`
- `session_end`

手工查看命令决策：

```bash
grep '"event": "command"' ~/.ssh_proxy/audit/<session_id>/events.jsonl
```

手工查看阻断事件：

```bash
grep '"command_blocked"' ~/.ssh_proxy/audit/<session_id>/events.jsonl
```

### replay.jsonl

记录终端回放事件：

- `stdout`：远端输出。
- `stdin_echo`：过滤模式下本地回显的用户输入。
- `stdin_control`：Ctrl-C / Ctrl-D / Ctrl-Z 等控制字符。

手工查看：

```bash
cat ~/.ssh_proxy/audit/<session_id>/replay.jsonl
```

### 脱敏

审计会对常见敏感内容做基础脱敏：

- `password=...`
- `passwd=...`
- `token=...`
- `secret=...`
- `api_key=...`
- `Bearer ...`

注意：第一版是基础 pattern 脱敏，不等于完整 DLP。生产环境建议接入公司统一 secret scanner。

## 大模型设备故障诊断接入

透明代理特别适合放在大模型和真实设备之间，作为安全执行层。

推荐链路：

```text
LLM Planner
  -> 生成只读诊断命令
ssh-proxy
  -> 策略检查、阻断危险命令、记录审计
设备 CLI
  -> 返回原始输出
LLM Analyzer
  -> 基于命令输出分析根因
ssh-proxy-audit
  -> 事后复盘命令、阻断和回放
```

### AI 可执行的命令范围

第一版建议让大模型只生成只读命令，例如：

```text
show version
show interface status
show logging
display clock
display interface brief
display logbuffer
dis current-configuration interface ...
ping ...
traceroute ...
```

代理会继续兜底阻断配置、删除、重启、保存等命令。即使大模型误生成危险命令，也不会发送到设备。

### AI 诊断推荐工作流

1. 用户输入故障现象和目标设备。
2. 大模型生成一组只读诊断命令。
3. 执行命令时统一包一层 `ssh-proxy`。
4. 代理放行安全命令，阻断危险命令。
5. 设备输出进入大模型分析。
6. 审计中保留完整证据链，供复盘和模型评估。

示例：

```bash
./ssh-proxy -- smc pam nd ssh 10.1.1.1
```

会话后查看 AI 或人工实际执行过什么：

```bash
./ssh-proxy-audit commands
./ssh-proxy-audit blocked
./ssh-proxy-audit replay
```

### 证据链价值

审计数据可以直接转成大模型诊断证据：

```text
device=10.1.1.1
time=2026-04-26T11:20:01+0800
command=display interface brief
decision=ALLOW
output=...
```

这比人工复制粘贴更可靠，也便于后续评估：

- 哪些命令有效。
- 哪些命令被阻断。
- 哪些命令重复。
- 哪些设备返回异常。
- 大模型是否产生危险命令倾向。

## 沙盒验证

已经提供了自动化沙盒测试，测试内容包括：

- fake device shell 正常显示 prompt。
- `show version` 被代理放行，并被 fake device 实际收到。
- `reload` 被代理阻断，fake device 没有收到。
- `display ` 的空格能即时到达 fake device，不需要回车。
- `show ?` 的 `?` 能即时到达 fake device，用于设备提示。
- `show <Tab>` 的 Tab 能即时到达 fake device，用于设备补全。
- `dis<Tab>` 会补成 `display `，避免终端把 Tab 展开成空格。
- 审计文件包含命令、阻断事件和终端回放。

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q \
  tests/test_ssh_proxy_policy.py \
  tests/test_ssh_proxy_audit.py \
  tests/test_ssh_proxy_sandbox_integration.py
```

预期：

```text
11 passed
```

只跑沙盒集成测试：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q tests/test_ssh_proxy_sandbox_integration.py
```

## 本地烟测

确认代理能启动任意子命令并写审计：

```bash
SSH_PROXY_AUDIT_DIR=/tmp/ssh-proxy-smoke ./ssh-proxy -- /bin/echo proxy-smoke
```

查看审计：

```bash
find /tmp/ssh-proxy-smoke -maxdepth 2 -type f
./ssh-proxy-audit --audit-dir /tmp/ssh-proxy-smoke list
./ssh-proxy-audit --audit-dir /tmp/ssh-proxy-smoke show
./ssh-proxy-audit --audit-dir /tmp/ssh-proxy-smoke replay
```

## 用户体验说明

登录阶段是透明透传：

- 密码输入照常显示或隐藏，由真实登录命令决定。
- MFA、yes/no、跳板提示照常交互。
- 代理不提前解析登录过程中的输入，避免误拦密码或验证码。

命令过滤阶段由代理本地接管输入行：

- 普通字符会实时发送到设备，尽量保持原生 CLI 体验。
- 安全前缀下，空格、`?`、Tab 不需要回车，会即时触发设备侧响应。
- 代理在空格、`?`、Tab、回车等边界点做保护，避免过早打断用户输入。
- 按 Enter 时仍会做最终策略判断，确保危险命令不会提交执行。

## 常见问题

### 为什么刚登录时不拦截？

为了避免误拦密码、MFA token、跳板交互。代理会等检测到远端 prompt 后再进入命令过滤模式。

### 为什么有些 prompt 识别不到？

第一版使用通用 prompt 识别规则，例如：

- `router#`
- `router>`
- `$`
- `<Huawei>`
- `[Huawei]`

如果内部设备 prompt 很特殊，可能需要扩展 `ssh_proxy/runner.py` 里的 `PROMPT_PATTERN`。

### 被阻断命令会不会已经发到远端？

不会。过滤模式下代理先本地缓冲输入行，按 Enter 判定，通过后才写入子进程 PTY。

### 如果用户 paste 很多命令怎么办？

代理会把多行输入拆开判定。只要出现危险行，整体阻断。

### 退出码是什么？

`metadata.json` 的 `exit_code` 是被代理登录命令的退出码。正常退出通常是 `0`。

### 是否支持真实 SSH？

支持，只要把真实 `ssh` 当作登录命令：

```bash
./ssh-proxy -- ssh user@10.1.1.1
```

但第一版目标是接管交互 CLI，不处理 `scp/sftp/rsync` 协议内容。

### 如何知道后台到底拦哪些命令？

使用策略查看工具：

```bash
./ssh-proxy-policy list
```

检查单条命令：

```bash
./ssh-proxy-policy check "show version"
./ssh-proxy-policy check "reload"
./ssh-proxy-policy check --config-mode "show version"
```

### 如何快速看某次会话里被拦了什么？

```bash
./ssh-proxy-audit blocked
```

如果不是最近一次会话，先列出 session：

```bash
./ssh-proxy-audit list
./ssh-proxy-audit blocked <session_id前缀>
```

## 当前文件

```text
ssh-proxy                         # 透明代理入口
ssh-proxy-audit                   # 审计查询入口
ssh-proxy-policy                  # 策略查看和离线检查入口
ssh_proxy/cli.py                  # 代理参数解析
ssh_proxy/runner.py               # PTY 接管和透明代理
ssh_proxy/policy.py               # 命令策略
ssh_proxy/policy_cli.py           # 策略 CLI
ssh_proxy/audit.py                # 审计写入
ssh_proxy/audit_store.py          # 审计读取和 session 解析
ssh_proxy/audit_cli.py            # 审计 CLI
tests/test_ssh_proxy_policy.py    # 策略测试
tests/test_ssh_proxy_policy_cli.py # 策略 CLI 测试
tests/test_ssh_proxy_audit.py     # 审计测试
tests/test_ssh_proxy_sandbox_integration.py  # 沙盒集成测试
```

## 生产化建议

第一版已经能完成透明接管、命令阻断和事后审计。生产化建议按这个顺序增强：

1. 将审计目录改为中心化路径或异步上传到审计服务。
2. 按设备类型拆分策略：Huawei / Cisco / Linux shell / Palo Alto。
3. 增加公司统一身份字段：员工 ID、工单号、审批单号、来源终端。
4. 增加策略配置文件，避免每次改规则都改代码。
5. 增加 Web 查询和会话回放。
6. 对特殊 prompt 和配置模式做厂商级状态机解析。
