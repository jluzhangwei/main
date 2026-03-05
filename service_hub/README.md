# SEA NOC Service Hub

统一管理以下三个服务的启动入口：

- LLDP Topology: `http://127.0.0.1:18080/lldp.html`
- Netlog Analyst: `http://127.0.0.1:8000/`
- HealthCheck Runner: `http://127.0.0.1:8080/`

## 启动

```bash
cd /Users/zhangwei/python/service_hub
./run.sh
```

默认管理页地址：`http://127.0.0.1:18888/`

首次启动会自动创建登录库：`service_hub/state/auth_db.json`。

- 默认管理员账号：`admin`
- 默认密码：`zhangwei`

登录后可在 `/admin` 创建角色和用户。

## 可选参数

- 指定管理页端口：`HUB_PORT=19999 ./run.sh`
- 启用开发热更新：`./run.sh --reload`

## 页面能力

- 统一两层导航外观（SEA NOC 样式）
- 三个服务 Icon 卡片
- 点击卡片后：
  - 后端执行对应服务启动命令
  - 前端自动打开服务 URL
  - 页面显示当前服务状态（未启动/启动中/运行中）
