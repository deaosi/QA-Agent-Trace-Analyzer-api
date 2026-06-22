# 店铺智能体训练工作台 - 云服务器部署

这套部署方式适合放到云服务器上，同事或朋友只需要访问一个网址即可使用后台。

## 1. 准备服务器

服务器需要安装：

- Docker
- Docker Compose

云服务器安全组需要放行你设置的端口，默认是 `5000`。

## 2. 上传项目

把整个项目目录上传到服务器，例如：

```bash
/opt/qa-agent-workbench
```

进入目录：

```bash
cd /opt/qa-agent-workbench
```

## 3. 配置访问密码

复制环境变量模板：

```bash
cp .env.example .env
```

编辑 `.env`：

```bash
QA_PORT=5000
QA_ADMIN_USERNAME=shuxing666
QA_ADMIN_PASSWORD=一段强密码
QA_ACCESS_PASSWORD=一段强密码
QA_SECRET_KEY=一段足够长的随机字符串
```

`QA_ADMIN_USERNAME` / `QA_ADMIN_PASSWORD` 是首次启动时自动创建的管理员账号。这个后台会保存店铺 Cookie、账号和分析数据，不建议裸奔开放到公网。

账号后台入口：

```text
http://服务器公网IP:5000/admin/users
```

管理员可以新增账号、修改密码、删除账号、禁用/启用账号、设置账号到期时间。普通用户可以从登录页自行注册，注册后管理员也可以在账号后台继续管理。

## 4. 启动服务

```bash
docker compose up -d --build
```

查看日志：

```bash
docker compose logs -f
```

访问：

```text
http://服务器公网IP:5000
```

如果绑定了域名，可以通过 Nginx 反向代理到 `127.0.0.1:5000`。

## 5. 数据保存位置

运行数据会保存在服务器项目目录下的 `data/`：

- Cookie
- 注册账号
- 店铺列表
- 拉取的 traces
- 分析结果
- 问题处理状态

升级代码或重建容器不会清空 `data/`，但迁移服务器时需要一起备份。

## 6. 常用命令

停止：

```bash
docker compose down
```

重启：

```bash
docker compose restart
```

更新代码后重新构建：

```bash
docker compose up -d --build
```

## 7. 不使用 Docker 的 Linux 部署方式

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r 111/requirements.txt
export QA_ACCESS_PASSWORD="你的访问密码"
export QA_SECRET_KEY="一段足够长的随机字符串"
export QA_DATA_DIR="/opt/qa-agent-workbench/data"
gunicorn -b 0.0.0.0:5000 --workers 2 --threads 4 --timeout 180 wsgi:app
```
