# 如何配置账号

此项目需要的是网易大神的守望先锋战绩业务凭据，不是战网客户端的登录 token。网页的“绑定”只保存你的 BattleTag，仍需先配置大神凭据。

## 1. 在自己的浏览器登录网易大神

打开 https://ds.163.com/ ，登录并确认大神账号已绑定自己的战网账号。

## 2. 获取 role_id

按照上游项目说明，进入网易暴雪充值中心的守望先锋页面或守望先锋官网对应页面，按 F12 打开开发者工具，切换到 Network（网络），按 Ctrl+F5 刷新，在请求中搜索 `role_id`（上游教程写的是 `&role_id`），找到属于自己账号的数字值。只需查看账号信息。

## 3. 获取大神战绩 token

在已登录的大神主页打开 F12 → Console（控制台），使用以下上游教程“第三步：获取 token”提供的脚本，把 `YOUR_ROLE_ID` 改成上一步的数字后自行运行：

https://github.com/AddOneSecondL/Overstats/blob/main/Faststart.md#第三步获取-token

该脚本使用大神页面自身的签名模块，向网易的 `https://inf.ds.163.com/v1/web/game/report/getReportToken` 请求自己的战绩令牌。成功后查看返回对象中的 `result.roleId` 和 `result.token`。不要使用网页自动生成的 `usr_...` 用户标识代替这个业务 token。

请直接在本机填写这两个值；不需要把 token、Cookie 或含有它们的截图发送到聊天里。上述原始教程还会打印其他会话字段，无需复制它们。

## 4. 保存到本机并重启

推荐双击本目录的 `configure_account.cmd`，按提示输入 role_id 和 token，脚本会保存 `.env` 并重启服务。token 输入时不会显示。

也可以直接编辑本目录被 Git 忽略的 `.env`：

```dotenv
OW_DASHEN_ROLE_ID=自己的数字role_id
OW_DASHEN_TOKEN=自己的大神战绩token
```

手动编辑后依次运行 `stop_local.cmd`、`start_local.cmd`。

## 5. 在网页绑定 BattleTag

打开 http://127.0.0.1:41888/ ，在顶部填 `昵称#数字` 并点“绑定”，然后查询资料、战绩、对局详情。

若之前已绑定但未配置大神凭据，可以直接重新查询；查询模块会根据 BattleTag 再解析玩家身份。实际账号是否能成功查询，需要用有效凭据联调确认。

AI 锐评另需配置模型 API Key；不影响普通战绩查询。
