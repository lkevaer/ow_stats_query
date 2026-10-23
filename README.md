# 守望先锋战绩查询 · 队友评分

基于 [DustEater/ow_stats_query](https://github.com/DustEater/ow_stats_query) 修改，保留完整查询服务、Web 页面和 Taro 客户端源码。

- 普通查询：资料、对局、英雄、段位历史等。
- **队友评分/上下等马查询**：上传截图或输入多个 BattleTag，分别查询近12场休闲或竞技，生成详细表格与可复制的简短文字。支持框选账号列表重试，有分歧的文字会标记并提供候选。
- 逐场英雄头像、地图图标、竞技段位与图标、红绿胜负箭头、真实组队标记，以及MVP金边、CARRY浅金边、SVP淡紫边；展示最后一击占比、Fleta、KD、伤害、治疗、阵亡与加减分。
- 阵亡结合双方整体数据判断；辅助Fleta达到40%较高加分，达到50%高权重加分且免除高阵亡惩罚。短于6.5分钟降权、长于15分钟小幅增权，双方最后一击接近时较明显增权。
- 近12场至少10胜标记“手感火热”并提高胜率权重，至少9场KD≥7较高加分。分制已校准，原70分约对应80分；S90、A80、B70、C60分起。近12场至少7场Fleta严格大于65%为SMURF，并保留最高权重专项加分。
- 页面保留原来的居中宽度与独立数据列，支持横向滚动；S/A/B/C/D分别使用淡金、淡紫、淡蓝、淡绿、灰色区分评分与等级。
- 不需要 AI 密钥即可查询及评分。

## Windows 运行

需要 Python 3.11 或更新版本。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements_for_api.txt
```

双击 `configure_account.cmd`，按 [LOGIN.md](LOGIN.md) 填入自己的网易大神业务凭据，随后服务自动启动。已配置账号时双击 `start_local.cmd`，停止使用 `stop_local.cmd`。

打开 [本机首页](http://127.0.0.1:41888/) 或 [队友评分](http://127.0.0.1:41888/batch-rating)。截图识别优先使用本地 RapidOCR PP-OCRv6，多次识别比对并对疑问行重试。首次使用会下载公开模型，图片始终在服务本地处理。Windows 系统 OCR 作为备用。

配置优先使用环境变量；没有 `api_server/config.yaml` 时会读取安全的 `config.yaml.example`，无需复制私有配置文件。

本地识别引擎的模型与使用说明见 [RapidOCR 官方文档](https://rapidai.github.io/RapidOCRDocs/main/install_usage/rapidocr/usage/)。

## Docker 运行

Docker 构建时会下载并自检增强中文模型，镜像内保留 Tesseract 简体中文与英文备用识别。模型文件随镜像保存，不上传到 Git 仓库。

```sh
cp .env.example .env
# 编辑 .env，填写自己的 OW_DASHEN_ROLE_ID 和 OW_DASHEN_TOKEN
docker compose up -d --build
```

入口同样为 `http://127.0.0.1:41888/batch-rating`。数据保存在 Docker 卷中。更新代码后再次运行上述启动命令；停止使用 `docker compose down`。

默认只绑定本机。需要外网访问时，使用 HTTPS 反向代理和访问控制；可通过 `OW_SERVER_API_KEY` 配置访问码，在评分页输入或在 API 请求携带 `X-API-Key`。账号令牌只写在本机或服务器的环境变量中。

## Linux / macOS

安装 Python 3.11+，执行 `bash start_api.sh`；停止使用 `bash stop_api.sh`。增强识别随 Python 依赖安装，备用识别可另装 Tesseract 与 `chi_sim`、`eng` 语言包。也可以直接输入 BattleTag。

## 评分与接口

上下等马只在本次查询名单内相对区分，不代表绝对水平；名单段位差距过大时没有比较意义。评分偏重最后一击与Fleta，Fleta仅表示个人最后一击占团队总数的两倍。普通胜率与综合KD小幅参考，持续高胜率和高KD有更明显加分。页面可展开完整文字评分参考，不展示算法系数。

使用说明和接口见 [BATCH_RATING.md](BATCH_RATING.md)，服务启动后也可打开 `/docs`。批量评分任务暂存内存一小时，服务使用单进程运行。

```sh
python -m unittest discover -s tests -v
node --check api_server/static/batch_rating.js
node --test tests/test_ocr_ui.cjs
node --test tests/test_rating_ui.cjs
```

GitHub Actions 会检查评分与查询逻辑，并构建、启动 Docker 镜像。GitHub 仓库存放完整代码；运行查询服务需要 Python 或 Docker，GitHub Pages 无法运行此后端。

QQ群机器人需求见 [BOT_BACKLOG.md](BOT_BACKLOG.md)，绑定自己、自动凑齐队伍与默认查询本人将在部署机器人时实现。

## 文件与授权

`.env`、账号配置、绑定信息、战绩数据库、日志、截图和查询报告均不应提交。运行缓存会自动创建，无需从他人电脑复制。

本项目沿用原仓库的 [GPL-3.0 许可证](LICENSE)。守望先锋相关名称与图像属于其权利人，战绩查询依赖网易大神的可访问接口和账号授权。
