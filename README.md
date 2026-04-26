# LLDP Topology Tool

LLDP Topology Tool 用于把多来源 LLDP 邻接数据转换成可操作的拓扑图，并在同一页面中完成链路分析、告警联动、设备信息补充、路径抽取和多格式导出。

项目提供两个主要页面：

- `lldp.html`：完整运维页，负责采集、分析、告警和导出
- `index.html`：轻量编辑页，负责整理已有拓扑数据并输出展示结果

## 页面分工

| 页面 | 主要定位 | 适合场景 |
| --- | --- | --- |
| `lldp.html` | 采集 + 分析 + 运维 | SQL / CLI / NDMP 导入、利用率查询、Zabbix 告警、Netlog 跳转、设备详情查询 |
| `index.html` | 编辑 + 整理 + 展示 | CSV / Json 导入、路径抽取、节点组合并、手工补图、导出汇报图 |

原则上：

- 需要实时采集或联动外部系统，用 `lldp.html`
- 已经有拓扑数据，只想做整理和展示，用 `index.html`

## 核心能力

### `lldp.html`

- 多来源导入：
  - SQL 导入
  - CLI 递归 LLDP 采集
  - NDMP 导入
  - CSV 文件导入 / 粘贴追加
  - Json 会话导入
- 拓扑生成与整理：
  - 设备身份归一
  - 链路去重与多链路合并
  - 路径查询、必经点、备份路径
  - 节点组与组视图
  - 手工节点、Cloud 节点、手工链路、链路描述
- 链路分析：
  - 当前利用率 / 区间利用率查询
  - 链路拥塞分析
  - 负载分担分析
  - TX / RX / 带宽标签显示
- 告警与日志联动：
  - Zabbix 告警查询
  - 活动告警高亮与时间过滤
  - 右键查看告警 / 刷新告警
  - 从告警设备跳转到 Netlog 创建页
- 设备详情：
  - 设备详情查询
  - 右键复制详情
  - 自定义字段 CSV 导出
- 导出与状态保存：
  - PNG
  - draw.io
  - Mermaid `.mmd`
  - 链路汇总 CSV
  - Json 会话导出
  - 服务端状态快照

### `index.html`

- 轻量导入：
  - CSV 导入 / 追加 / 粘贴
  - Json 导入 / 追加
- 纯前端拓扑整理：
  - 路径查询
  - 节点组与组视图
  - 节点颜色自定义
  - 手工节点 / Cloud 节点 / 手工链路
  - 链路描述
  - 对齐、等间距、局部重排
  - 隐藏 / 只显示 / 单链路节点筛选
- 导出：
  - PNG
  - draw.io
  - Mermaid 复制到剪贴板
  - 链路汇总 CSV
  - Json 导出
- 状态能力：
  - 服务端状态保存 / 导入
  - 撤销 / 恢复

## 导入能力矩阵

| 能力 | `lldp.html` | `index.html` |
| --- | --- | --- |
| SQL 导入 | Yes | No |
| CLI 导入 | Yes | No |
| NDMP 导入 | Yes | No |
| CSV 导入 | Yes | Yes |
| CSV 粘贴 / 追加 | Yes | Yes |
| Json 导入 | Yes | Yes |
| Json 追加 | Yes | Yes |
| Zabbix 利用率查询 | Yes | 轻量模式默认隐藏入口 |
| Zabbix 告警流程 | Yes | No |
| Netlog 告警跳转 | Yes | No |

## 快速启动

### 方案一：本地 Python 服务

这是当前最推荐的方式，适合开发、调试，以及同时使用 `lldp.html` 和 `index.html`。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.mysql.example .env.mysql
./start_lldp_service.sh start
```

访问入口：

- `http://127.0.0.1:18080/lldp.html`
- `http://127.0.0.1:18080/index.html`

常用命令：

```bash
./start_lldp_service.sh status
./start_lldp_service.sh logs
./start_lldp_service.sh restart
./start_lldp_service.sh stop
```

### 方案二：Docker Compose

```bash
cp .env.mysql.example .env.mysql
docker compose up -d --build
```

当前注意事项：

- 当前 Docker 镜像优先覆盖核心服务和 `lldp.html` 主流程。
- 如果你希望在容器内同时直接访问 `index.html`，需要确认镜像中已包含 `index.html` 及相关资源，或通过挂载工作目录提供这些文件。

## 配置说明

配置默认从 `.env.mysql` 读取，也可以通过 `DB_ENV_FILE` 指向其他文件。

示例文件：`.env.mysql.example`

### SQL 导入必填

```env
DB_HOST=...
DB_PORT=...
DB_USER=...
DB_PASSWORD=...
DB_NAME=...
```

### CLI / SMC 跳板可选

```env
CLI_DEVICE_USERNAME=...
CLI_DEVICE_PASSWORD=...
SMC_JUMP_HOST=...
SMC_JUMP_PORT=22
SMC_COMMAND=smc server toc {jump_host}
CLI_CONNECT_TIMEOUT=60
CLI_COMMAND_TIMEOUT=45
```

### Zabbix 相关可选

可通过页面配置，也可通过环境变量注入部署默认值：

- `ZABBIX_URL`
- `ZABBIX_API_TOKEN`
- `ZABBIX_VERIFY_SSL`

## 项目结构

```text
./
├── lldp.html                              # 完整运维页
├── index.html                             # 轻量编辑页
├── lldp_sql_service.py                    # FastAPI 后端
├── start_lldp_service.sh                  # 本地启动脚本
├── lldp-manual.html                       # 内置使用手册
├── LLDP_TOPOLOGY_TOOL_FUNCTION_DESIGN.md  # 中文完整设计文档
├── LLDP_TOPOLOGY_TOOL_FUNCTION_DESIGN_EN.md
├── INDEX_HTML_USAGE_GUIDE.md              # index.html 详细说明
├── Topology_CSV_Logic.md                  # CSV 映射与去重逻辑
├── shared/                                # 共享页头与静态资源
├── tmp_csv/                               # 临时 CSV 输出目录
└── state_snapshots/                       # 服务端状态快照目录
```

## 典型工作流

### 工作流 A：实时采集并分析拓扑

1. 打开 `lldp.html`
2. 通过 SQL / CLI / NDMP 导入拓扑数据
3. 生成拓扑并做必要整理
4. 查询链路利用率、告警或设备详情
5. 使用过滤、分组、路径查询收口重点范围
6. 导出 PNG / draw.io / Mermaid / Json / 链路汇总

### 工作流 B：整理已有拓扑并输出汇报图

1. 打开 `index.html`
2. 导入 CSV 或 Json
3. 生成拓扑并调整局部布局
4. 使用节点组、路径查询、Cloud 节点、手工链路和链路描述补充语义
5. 用对齐与等间距整理版式
6. 导出展示结果

## 导出行为说明

为了避免文档和页面行为混淆，这里单独说明：

- `lldp.html`
  - `draw.io`：下载文件
  - `Mermaid`：下载 `.mmd` 文件
- `index.html`
  - `draw.io`：下载文件
  - `Mermaid`：复制到剪贴板

## 运行期文件与 Git 管理建议

以下内容属于本地配置、运行期缓存或环境产物，不应作为共享源码提交：

- `.env.mysql`
- `zabbix_config.json`
- `tmp_csv/`
- `state_snapshots/`
- `.lldp_service.pid`
- `.lldp_service.log`
- 本地备份文件，例如 `goodindex.html`、`lldp_副本.html`

建议仅提交：

- 页面源码
- 后端服务代码
- 启动脚本
- 文档
- 配置样例

## 相关文档

- 中文完整设计文档：`LLDP_TOPOLOGY_TOOL_FUNCTION_DESIGN.md`
- 英文完整设计文档：`LLDP_TOPOLOGY_TOOL_FUNCTION_DESIGN_EN.md`
- `index.html` 说明：`INDEX_HTML_USAGE_GUIDE.md`
- CSV 逻辑说明：`Topology_CSV_Logic.md`
- 页面内置手册：`lldp-manual.html`

## 文档边界说明

- `README.md` 负责项目入口说明。
- 两份 `LLDP_TOPOLOGY_TOOL_FUNCTION_DESIGN*` 文档负责详细设计与实现说明。
- `INDEX_HTML_USAGE_GUIDE.md` 只聚焦轻量编辑页，不覆盖完整运维页。
