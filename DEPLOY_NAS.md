# NAS Docker 部署说明

这个项目可以直接用 Docker Compose 部署。应用端口是 `8006`。当前 compose 会把整个项目目录挂载进容器，所以代码更新后重启容器即可生效。

## 1. 上传项目到 NAS

把整个项目文件夹上传到 NAS，例如：

```text
/volume1/docker/jiede-web
```

需要一起上传的运行数据：

```text
data/manuals.db
manuals/
uploads/
```

## 2. 准备环境变量

在 NAS 项目目录里复制一份配置：

```sh
cp .env.example .env
```

然后修改 `.env`，至少改这几项：

```env
ADMIN_USERNAME=admin
ADMIN_PASSWORD=你的后台密码
SECRET_KEY=换成一串随机长字符串
MAX_UPLOAD_MB=1024
```

如果要使用邮件发送功能，再填写 `SMTP_*` 配置。

## 3. 启动服务

在项目目录执行：

```sh
docker compose up -d --build
```

启动后访问：

```text
http://NAS的IP:8006
```

## 4. 更新项目

以后更新代码后，在项目目录执行：

```sh
docker compose restart
```

如果你改了依赖或 Dockerfile，再执行：

```sh
docker compose up -d --build
```

数据会保留在 NAS 的这些目录中：

```text
data/
manuals/
uploads/
```

## 5. 备份建议

定期备份下面这些位置即可：

```text
data/manuals.db
manuals/
uploads/
```
