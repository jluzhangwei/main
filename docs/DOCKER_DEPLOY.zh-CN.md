# Docker 部署说明

本文档用于将 `netops-ai-v1` 作为一套完整服务通过 Docker 启动。

## 目标

- 一条命令启动前后端
- 前端通过 Nginx 对后端反向代理
- 后端本地状态持久化到 Docker volume
- 用户只需要访问一个前端地址即可

## 容器组成

- `backend`
  - FastAPI 服务
  - 对外端口：`8000`
- `frontend`
  - Vite 构建产物 + Nginx
  - 对外端口：`5173`

说明：

- 当前项目运行时核心数据仍以内存 + 本地快照为主。
- `docker-compose.yml` 中保留了 `NETOPS_POSTGRES_DSN` 和 `NETOPS_REDIS_URL` 环境变量，便于后续扩展，但当前默认部署不强依赖 Postgres / Redis 容器。

## 持久化目录

容器内通过：

- `HOME=/data`

让以下数据落到 Docker volume `netops_data` 中：

- LLM 配置
- SOP 档案库
- 命令执行纠正规则
- 会话持久化文件

这意味着：

- `docker compose down` 不会丢数据
- `docker compose down -v` 会清空持久化数据

## 启动

在项目根目录执行：

```bash
cd /Users/zhangwei/Documents/Python/netops-ai-v1
docker compose up -d --build
```

也可以直接用项目自带脚本：

```bash
cd /Users/zhangwei/Documents/Python/netops-ai-v1
./docker_netops.sh start
```

启动后：

- 前端：[http://127.0.0.1:5173](http://127.0.0.1:5173)
- 后端 OpenAPI：[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

## 停止

```bash
docker compose down
```

或：

```bash
./docker_netops.sh stop
```

## 完整清理

```bash
docker compose down -v
```

或：

```bash
./docker_netops.sh reset
```

说明：

- 这会删除 `netops_data` 卷
- 包括 LLM 配置、SOP 草稿/发布记录、命令执行纠正规则等持久化数据

## 查看日志

查看全部：

```bash
docker compose logs -f
```

或：

```bash
./docker_netops.sh logs
```

仅看后端：

```bash
docker compose logs -f backend
```

仅看前端：

```bash
docker compose logs -f frontend
```

## 更新代码后重建

```bash
docker compose up -d --build
```

## 访问方式

### 1. 浏览器访问

- 建议直接打开前端：
  - [http://127.0.0.1:5173](http://127.0.0.1:5173)

前端 Nginx 已自动代理这些接口到后端：

- `/api/*`
- `/v1/*`
- `/v2/*`
- `/health`
- `/docs`
- `/openapi.json`

### 2. 第三方脚本 / API 调用

可直接访问后端：

- `http://127.0.0.1:8000/api/runs`

也可通过前端反向代理入口访问相同 API：

- `http://127.0.0.1:5173/api/runs`

建议：

- 自动化脚本和第三方系统优先直接访问 `8000`
- 浏览器内置页面走 `5173`

## 首次检查

### 检查后端健康状态

```bash
curl -sS http://127.0.0.1:8000/health
```

期望输出：

```json
{"status":"ok"}
```

### 检查前端首页

```bash
curl -I http://127.0.0.1:5173
```

期望返回：

- `HTTP/1.1 200 OK`

## 常见问题

### 1. 页面能打开，但接口报 `Failed to fetch`

先看后端是否正常：

```bash
docker compose logs -f backend
```

再检查：

```bash
curl -sS http://127.0.0.1:8000/health
```

### 2. API 返回 `json_invalid`

说明请求体 JSON 不合法。常见原因：

- 使用了中文引号 `“ ”`
- `X-API-Key` 里把 `< >` 一起复制进去了

### 3. API 返回 `API key missing or invalid`

说明传入的是：

- 错误 Key
- 或者只是 Key 前缀，不是完整 Key

正确做法：

- 在 `第三方 Key 服务中心` 创建或轮换 Key
- 复制完整 Key
- 用完整 Key 放进 `X-API-Key`

### 4. 容器重建后配置没了

如果你执行过：

```bash
docker compose down -v
```

那是预期行为，因为 volume 被一起删掉了。

## 推荐操作顺序

1. `docker compose up -d --build`
2. 打开 [http://127.0.0.1:5173](http://127.0.0.1:5173)
3. 在 `AI 设置` 中配置模型
4. 在 `第三方 Key 服务` 中创建第三方 Key
5. 在 `连接控制` 中创建会话开始诊断
