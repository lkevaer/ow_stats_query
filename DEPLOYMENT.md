# 固定部署版本：deploy-2026.10.24

此标签保存当前网页查询系统：上下等马首页、近12场评分、OCR、浏览器账号绑定、MVP/CARRY/SVP、SSS及SMURF展示、成功查询ID日志。保留现有查询API。QQ群机器人不在本部署包的运行范围内。

仓库中的部署文件、前后端源码、字体和图片资源均包含在代码包中。账号Token、`.env`、实际配置、数据库、绑定和查询日志不随版本发布。OCR模型在构建镜像时自动下载并完成中文识别自检，因此首次构建需要网络。

## 首次部署

准备好 Docker 和 Docker Compose，在服务器执行：

```sh
git clone --branch deploy-2026.10.24 --single-branch https://github.com/lkevaer/ow_stats_query.git
cd ow_stats_query
cp .env.example .env
```

编辑 `.env`，填写自己的 `OW_DASHEN_ROLE_ID` 和 `OW_DASHEN_TOKEN`。对外提供服务时填写 `OW_SERVER_API_KEY` 作为访问码，然后启动：

```sh
docker compose up -d --build
```

首页是 `http://127.0.0.1:41888/`，旧 `/batch-rating` 自动跳转。默认只监听服务器本机，公网域名沿用现有 HTTPS 反向代理配置。

也可下载本标签的 ZIP，解压后进入包含 `compose.yaml` 的目录，从复制配置示例这一步开始操作。

## 已有服务器固定到这一版

先备份现有配置和数据，并处理服务器尚未提交的源码修改。在原项目目录中执行：

```sh
git fetch origin tag deploy-2026.10.24
git switch --detach deploy-2026.10.24
docker compose up -d --build
```

沿用原目录、`.env` 和原 Compose 项目名，以继续使用现有 `ow-data`、`ow-bindings` 数据卷。更新无需删除数据卷。

## 验证

```sh
docker compose ps
curl -fsS http://127.0.0.1:41888/ready
docker compose logs --tail=50 ow-stats
```

打开网页并查询一个有效账号，确认能返回评分。首次中文OCR查询可能包含模型加载时间；实际查询仍依赖有效账号授权和上游接口可用。

成功查询ID日志保存在容器 `/app/data/successful_battletags.txt`；500MB触发清理，保留最新不超过400MB的完整记录。代码、配置及日志说明详见 [README](README.md) 和 [LOGIN](LOGIN.md)。
