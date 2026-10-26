# 开发与验证

使用Python 3.12，创建独立虚拟环境后执行 `python -m pip install -r requirements_for_api.txt`。运行依赖受`constraints-api.txt`约束；升级依赖时同时更新约束并运行回归检查，不将开发机其他项目的依赖加入运行清单。

网页入口为`api_server/main.py`，页面为`api_server/static/`中的原生HTML/CSS/JavaScript。遗留`src/`、Taro配置与其npm构建脚本不参与当前产品；Node只需用于内置测试，无需执行`npm install`。

```sh
python -m unittest discover -s tests -v
node --test tests/test_ocr_ui.cjs tests/test_rating_ui.cjs tests/test_rating_account.cjs tests/test_game_assets.cjs
```

外部接口不可用时，以匿名、可控完整数据验证缓存、队列和错误行为，不伪造实时查询成功。回归数据不得包含真实BattleTag、QQ用户标识、令牌或原始群截图。算法变化应记录规则版本，并同时验证角色差异、样本权重和缺失数据。

机器人位于独立仓库，改动后端接口时同时验证其`OWClient`、错误格式、轮询时限、服务凭据和长图渲染。固定UI和本地图标一起同步，更新其资源哈希清单。

提交前检查Git差异。不要提交`.env`、运行配置、数据库、Cookie、日志、OCR原图、查询报告或私有备份。后端防御与部署细节写在维护文档，不添加到页面更新日志、公告或用户报告。
