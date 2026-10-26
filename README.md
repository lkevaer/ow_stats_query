# 守望先锋战绩查询 · 队友评分

基于 [DustEater/ow_stats_query](https://github.com/DustEater/ow_stats_query) 修改。网站首页直接提供队友评分，保留原查询接口及 Taro 客户端源码。

部署与已有服务器更新步骤见 [DEPLOYMENT.md](DEPLOYMENT.md)。整局名单查询、韩服英雄胜率页面和缓存接口见 [HERO_RATES.md](HERO_RATES.md)。

- 首页展示队友评分，顶部新增“韩服英雄胜率数据”入口；资料、对局、英雄、段位历史等接口继续可用。
- 每场详情最左侧“查询当局”自动填入两队完整战网ID并开始新一轮查询，支持10人及12人对局，不额外加入局外绑定账号；名单不完整时禁用。
- 韩服英雄胜率页默认大师、全图、PC、韩服，按胜率降序，并保留原站全部筛选项。选取率、胜率、禁用率及头像使用原站数据，英雄名称使用国服名称。
- 统计按筛选组合持久缓存24小时，默认组合预热、其他组合首次建立缓存，之后后台定时更新；角色切换与排序不触发重新抓取。刷新失败保留旧数据及真实缓存时间。
- **队友评分/上下等马查询**：上传截图或输入多个 BattleTag，分别查询近12场休闲或竞技，生成详细表格与可复制的简短文字。支持框选账号列表重试，有分歧的文字会标记并提供候选。
- 逐场英雄头像、地图图标、竞技段位与图标、红绿胜负箭头、真实组队标记，以及MVP金边、CARRY浅金边、SVP淡紫边；展示最后一击占比、Fleta、KD、伤害、治疗、阵亡与加减分。
- 阵亡结合双方整体数据判断；辅助Fleta达到40%较高加分，达到50%高权重加分且免除高阵亡惩罚。短于6.5分钟降权、长于15分钟小幅增权，双方最后一击接近时较明显增权。
- 近12场至少10胜标记“手感火热”并提高胜率权重，至少9场KD≥7较高加分。100分为SSS；S90、A80、B70、C60分起。近12场至少7场Fleta严格大于65%为SMURF，并保留最高权重专项加分。
- 右上角可绑定一个战网ID、解绑，开关控制每次查询是否自动加入本人；合并去重后最多10人。绑定与开关保存在当前浏览器的Cookie中一年，不按IP共享，不读取MAC地址；清除本站数据后需重新绑定。绑定仅设置查询目标，不验证账号所有权。
- 单人保留评分、等级和逐场详情，不分上下等马，提示增加ID比较；至少两位有效评分玩家时显示相对分档。
- 页面保留居中宽度与独立数据列；鼠标在超宽表格内滚轮横向滚动，移到两侧正常上下滚动。评级使用更清楚的金、紫、蓝、绿、灰色；MVP金色流光、S金色细线、SSS红色细线与SMURF红色徽标有克制动效，支持减少动态效果偏好。短局、长局、势均力敌与碾压使用淡色图标徽标。
- 不需要 AI 密钥即可查询及评分。

## Windows 运行

需要 Python 3.11 或更新版本。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements_for_api.txt
```

双击 `configure_account.cmd`，按 [LOGIN.md](LOGIN.md) 填入自己的网易大神业务凭据，随后服务自动启动。已配置账号时双击 `start_local.cmd`，停止使用 `stop_local.cmd`。

打开 [本机首页](http://127.0.0.1:41888/)，直接进入队友评分。旧 `/batch-rating` 地址自动跳转到首页。截图识别优先使用本地 RapidOCR PP-OCRv6，多次识别比对并对疑问行重试。首次使用会下载公开模型，图片始终在服务本地处理。Windows 系统 OCR 作为备用。

配置优先使用环境变量；没有 `api_server/config.yaml` 时会读取安全的 `config.yaml.example`，无需复制私有配置文件。

本地识别引擎的模型与使用说明见 [RapidOCR 官方文档](https://rapidai.github.io/RapidOCRDocs/main/install_usage/rapidocr/usage/)。

## Docker 运行

Docker 构建时会下载并自检增强中文模型，镜像内保留 Tesseract 简体中文与英文备用识别。模型文件随镜像保存，不上传到 Git 仓库。

```sh
cp .env.example .env
# 编辑 .env，填写自己的 OW_DASHEN_ROLE_ID 和 OW_DASHEN_TOKEN
docker compose up -d --build
```

入口同样为 `http://127.0.0.1:41888/`。数据保存在 Docker 卷中。更新代码后执行 `git pull --ff-only`，再运行 `docker compose up -d --build`；只拉取代码不会更新已经运行的容器。停止使用 `docker compose down`。

网页会根据文件内容自动更新CSS与脚本地址，并校验文件完整性，避免新页面配到浏览器缓存的旧样式。首页要求重新验证缓存；若使用反向代理/CDN，请让首页遵循响应的缓存设置，并保留静态资源URL中的查询参数。Docker检查同时验证实际镜像中的页面、脚本、样式和绘图图片资源。

默认只绑定本机。需要外网访问时，使用 HTTPS 反向代理和访问控制；可通过 `OW_SERVER_API_KEY` 配置访问码，在评分页输入或在 API 请求携带 `X-API-Key`。账号令牌只写在本机或服务器的环境变量中。

## Linux / macOS

安装 Python 3.11+，执行 `bash start_api.sh`；停止使用 `bash stop_api.sh`。增强识别随 Python 依赖安装，备用识别可另装 Tesseract 与 `chi_sim`、`eng` 语言包。也可以直接输入 BattleTag。

## 评分与接口

上下等马只在本次查询名单内相对区分，不代表绝对水平；名单段位差距过大时没有比较意义。评分偏重最后一击与Fleta，Fleta仅表示个人最后一击占团队总数的两倍。普通胜率与综合KD小幅参考，持续高胜率和高KD有更明显加分。页面可展开完整文字评分参考，不展示算法系数。

使用说明和接口见 [BATCH_RATING.md](BATCH_RATING.md)，需要API文档时可在本机设置 `OW_ENABLE_DOCS=1` 后打开 `/docs`。同一战网ID、同一模式的原始战绩在5分钟内复用，之后重新获取；刷新失败保留并标注未满24小时的旧数据，达到24小时后不再使用并自动清理。报告编号独立生成，内存保留一小时，落盘报告最多保留24小时。服务使用单进程运行。

搜索框旁可保存、删除和多选常用ID；选中名单与输入内容、本人账号合并去重后查询。记录仅存当前浏览器本地。英雄、地图和段位图标已随代码本地化，包含分类占位图；韩服页面共用英雄头像，不依赖玩家浏览器访问外部CDN。维护资源使用 `python scripts/sync_game_assets.py`。

```sh
python -m unittest discover -s tests -v
node --check api_server/static/batch_rating.js
node --test tests/test_ocr_ui.cjs
node --test tests/test_rating_ui.cjs
node --test tests/test_rating_account.cjs
node --test tests/test_game_assets.cjs
```

GitHub Actions 会检查评分与查询逻辑，并构建、启动 Docker 镜像。GitHub 仓库存放完整代码；运行查询服务需要 Python 或 Docker，GitHub Pages 无法运行此后端。

公开使用的限流、统一队列、管理权限、旧缓存降级、资源预算和部署验收见 [PUBLIC_OPERATIONS.md](PUBLIC_OPERATIONS.md)。API文档默认关闭；完成的旧版本报告显示当时的评分版本，重新查询使用当前算法。

QQ群机器人已在独立项目 [astrbot_plugin_ow_stats](https://github.com/lkevaer/astrbot_plugin_ow_stats) 实现帮助、绑定/解绑、竞技/休闲、截图多人查询与韩服数据长图。安装与服务凭据配置以机器人仓库说明为准。

开发入口是 `api_server/main.py` 与 `api_server/static/`。根目录遗留的 Taro 模板不参与当前网页构建；检查前端使用 Node 内置测试，无需安装模板依赖。开发流程见 [CONTRIBUTING.md](CONTRIBUTING.md)，素材来源见 [THIRD_PARTY.md](THIRD_PARTY.md)。

## 文件与授权

成功查询的完整战网ID自动记录在 `data/successful_battletags.txt`；首次成功查询后创建，历史记录无法补录。每行仅包含北京时间（含 `+08:00`）和一个 BattleTag，以制表符分隔，不写入token、访问码、IP或Cookie。重复成功查询会保留新的记录，同一次批量请求中的重复ID只写一次。

批量评分只记录实际取得有效评分的玩家；查不到公开数据、失败、仅提交任务、轮询和网页绑定都不写入。保留的普通查询、流式与异步查询接口也会在成功后记录；本地玩家搜索记录成功返回的完整ID。

文件达到500 MB（500,000,000字节）时，删除最早内容，保留最近不超过400 MB的完整记录。清理使用分块复制与原子替换，避免从中文字符中间截断；多个查询写入会串行处理，服务仍按单进程运行。日志写入或清理失败会在运行日志报错，不把成功查询改成失败。

Docker 内的路径是 `/app/data/successful_battletags.txt`，保存在已有的 `ow-data` 数据卷中。可导出到当前目录：

```sh
docker compose cp ow-stats:/app/data/successful_battletags.txt ./successful_battletags.txt
```

可选环境变量 `OW_QUERY_ID_LOG_FILE` 用于指定其他日志路径；容器部署时应选择持久化目录。该文件及清理临时文件已加入Git和Docker忽略规则，也不通过网页提供下载。

`OW_QUERY_ID_LOG_ENABLED=0` 可以关闭成功ID记录。此日志与原始战绩缓存分开：日志按容量轮转，原始战绩及报告最多保留24小时。

`.env`、账号配置、绑定信息、战绩数据库、日志、截图和查询报告均不应提交。运行缓存会自动创建，无需从他人电脑复制。

本项目沿用原仓库的 [GPL-3.0 许可证](LICENSE)。守望先锋相关名称与图像属于其权利人，战绩查询依赖网易大神的可访问接口和账号授权。
