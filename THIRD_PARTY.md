# 来源与权利说明

- 项目基于[原始ow_stats_query](https://github.com/DustEater/ow_stats_query)，代码沿用仓库GPL-3.0许可证。
- 韩服英雄统计来自[Nexon官方英雄数据页](https://overwatch.nexon.com/hero/rate)，以实际筛选、抓取时间和数据源可用性为准。
- 英雄中文名称与游戏图像由游戏官方配置/公开素材整理；本地图标同步入口为`scripts/sync_game_assets.py`，映射为`api_server/static/game_assets.js`。游戏名称、头像、地图、段位及横幅的权利属于其权利人；代码许可证不表示已取得素材再分发或商业授权。
- 战绩来源为网易大神可访问接口，运行者负责核对账号授权、接口条款及公开服务使用条件。本项目的评分是独立计算结果，不是官方评级。
- 运行依赖见`requirements_for_api.txt`、`constraints-api.txt`；OCR由RapidOCR及ONNX Runtime执行，模型与相关依赖保留各自许可，不能用本项目代码许可证替代。

大规模公开或商业化前，应由运营者完成数据及素材授权核查。此来源清单不是授权证明。
