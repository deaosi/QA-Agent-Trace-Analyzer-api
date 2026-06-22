# Windows 云服务器部署说明

## 推荐流程

把整个项目文件夹上传到 Windows 云服务器。

第一次部署，双击：

```text
deploy_only.bat
```

或中文入口：

```text
部署环境.bat
```

部署完成后启动服务，双击：

```text
start_server.bat
```

或中文入口：

```text
启动服务.bat
```

以后日常只需要双击 `start_server.bat` 启动，不需要重复部署。

## 管理面板

双击：

```text
manage_server.bat
```

或中文入口：

```text
服务管理.bat
```

可以执行：

- 部署环境
- 启动服务
- 停止服务
- 重启服务
- 打开本机后台
- 打开管理员后台
- 查看服务日志

## 兼容一键入口

如果你仍想一次完成部署并启动，可以双击：

```text
deploy_start.bat
```

或：

```text
一键部署并启动.bat
```

## 访问地址

启动后访问：

```text
http://服务器公网IP:5000
```

管理员后台：

```text
http://服务器公网IP:5000/admin/users
```

默认管理员：

```text
账号：查看 .env 中的 QA_ADMIN_USERNAME
密码：查看 .env 中的 QA_ADMIN_PASSWORD
```

## 开放外部访问

云服务器安全组放行：

```text
TCP 5000
```

Windows 防火墙需要管理员 CMD 执行：

```bat
netsh advfirewall firewall add rule name="QA Agent Workbench 5000" dir=in action=allow protocol=TCP localport=5000
```

## 数据位置

数据默认保存在：

```text
项目目录\data
```

迁移服务器或备份时保留 `data/`。

## 修改端口或默认账号

编辑 `.env`：

```text
QA_PORT=5000
QA_ADMIN_USERNAME=shuxing666
QA_ADMIN_PASSWORD=一段强密码
QA_ACCESS_PASSWORD=一段强密码
QA_SECRET_KEY=change-this-to-a-long-random-string
```

如果已经生成过 `data/.users.json`，修改 `.env` 不会覆盖已有账号。需要进入管理员后台改账号，或删除 `data/.users.json` 后重启重新初始化。
