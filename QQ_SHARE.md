# QQ XML 图文卡片

可直接复制的消息代码见 [qq_share_card.xml](qq_share_card.xml)，使用 `serviceID=1`、`action=web` 和 `item layout=2` 的网站图文卡片结构；封面为项目的逐场战绩详情图片，点击跳转到网站。

将 XML 导入支持原始 XML 消息的卡片发送工具，消息类型选择 XML。普通 QQ 聊天框粘贴代码只会成为文本。模板结构参考 [MiraiGo 的 NewUrlShare 实现](https://github.com/Mrs4s/MiraiGo/blob/master/message/elements.go)及 [OneBot 11 XML 消息段](https://github.com/botuniverse/onebot-11/blob/master/message/segment.md#xml-%E6%B6%88%E6%81%AF)；不同客户端和发送器的支持情况可能不同，未进行实际QQ发送测试。

服务器拉取本次更新并重新构建后，先确认 `https://lkevaer.xyz/static/share-cover.png` 能公开打开，再发送 XML。否则 QQ 无法取得封面。本次只生成卡片资料，没有启动机器人或发送群消息。

标题：守望先锋｜队友评分·上下等马查询

简介：截图识别或输入战网ID，查看近12场 Fleta、MVP 等数据，对比队友相对评分。

入口：[lkevaer.xyz](https://lkevaer.xyz/)

封面：[战绩详情封面](api_server/static/share-cover.png)。封面以密集的逐场战绩表格为主体，数据用于展示功能，不代表所有玩家的表现。

服务器更新并重新构建后，封面公开地址是 `https://lkevaer.xyz/static/share-cover.png`。首页已附带标题、简介、封面等分享元数据，遵循 [Open Graph](https://ogp.me/) 格式。

先在浏览器确认封面地址能打开，再在 QQ 中分享首页。是否生成原生图文卡片、采用何种封面和排版，由 QQ 客户端与其缓存决定，网页元数据不能强制控制。也可直接向群中发送封面图片与网站链接。普通聊天框粘贴 JSON 不会自动生成原生卡片。

若部署到其他域名，请同步修改首页中的分享网址和封面地址。
