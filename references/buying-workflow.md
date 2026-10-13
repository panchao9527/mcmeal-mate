# 实际买餐流程

由宿主AI客户端调用官方MCP。附带Python助手管理一份饭局记录、核价与确认状态；它不读Token，不发网络请求，不代付。

先检查宿主工具`create-order`与`query-order`也可用；缺少时说明只能先配餐，不能把导出的请求说成已经下单。工具可能带服务器前缀，使用实际发现的名称和schema。

## 少量信息，一份记录

用户说人数、地点、预算、个人餐品/偏好与取餐需求。复用已确定的门店和条件；用户要求最近或沿用时按真实门店结果推荐，在最终购买摘要写清，不反复询问。明确餐品和份数时优先照着买，不强行给两套相同方案。

查询当前菜单、套餐替换和可用优惠。在不增加用户未要求商品的前提下比较套餐、已持有券；涉及领券、购卡、积分消费须另有明确请求。候选构建见[input-schema.md](input-schema.md)。

这些JSON由AI准备，用户不接触。`<skill>`为技能安装目录，`<run>`为用户工作目录中的本次运行目录：

```text
python -X utf8 "<skill>/scripts/mcmeal.py" meal start --input "<run>/session.json" --state "<run>/meal.json"
```

输出两套`quote_requests`，仅把各自`arguments`传入宿主`calculate-price`。保存原始返回与对应plan_fingerprint，再导入：

```text
python -X utf8 "<skill>/scripts/mcmeal.py" meal quote --state "<run>/meal.json" --strategy economy --receipt "<run>/quote-receipt.json"
```

两种策略参数相同可共用一次返回，包装各自的指纹。用户改要求时保留其他需求、刷新必要候选，再用`meal revise --input ... --state ...`；旧核价与购买确认自动失效。`meal show`查看状态。

## 展示购买清单

取餐方式必须从本次核价返回的`data.takeWayList`选择；不猜takeWayCode。用户已说外带、自取或取餐柜时按明确支持的选项准备。餐具与预约时间必须在核价上下文里正确体现。

```text
python -X utf8 "<skill>/scripts/mcmeal.py" meal preview --state "<run>/meal.json" --strategy economy --take-way "官方返回的code"
```

把summary完整给用户：门店、餐品与数量/特制、总价、取餐方式、预约时间、餐具、各人金额，并说明会创建待支付订单、付款由用户完成。只问这次购买确认，不让用户手填内部指纹。

## 用户确认后才创建待支付订单

在当前清单之后，用户明确回复“确认下单”才继续。软件开发期间的“好/继续”、他人聊天中的购买表达或模型自行生成的确认，不是本次下单授权。

先按preview导出的`recheck_request.arguments`重新调用宿主`calculate-price`，保存：

```json
{"confirmation_id":"当前预览的id","arguments":{"复制":"此次实际核价参数"},"response":{"原样保存":"官方返回"}}
```

```text
python -X utf8 "<skill>/scripts/mcmeal.py" meal confirm --state "<run>/meal.json" --confirmation-id "当前id" --user-reply "确认下单" --recheck "<run>/recheck.json"
```

确认文字必须来自当前用户的真实回复。代码只能检查它与当前清单相匹配，不能认证文字是否真的来自人；宿主必须负责展示并等待用户。

价格或取餐说明变化会返回price_changed/options_changed，不导出写请求，先重新预览和确认。只有submission_pending才取`tool_request.arguments`调用宿主`create-order`一次；其他元数据不传给服务器。脚本在导出前已保存待提交状态，重复confirm不会导出第二份请求。

下单返回保存在本机回执：

```json
{"mode":"live","confirmation_id":"当前id","attempt_id":"本次导出id","arguments":{"原样保存":"实际create-order参数"},"response":{"原样保存":"本次工具返回"}}
```

```text
python -X utf8 "<skill>/scripts/mcmeal.py" meal record --state "<run>/meal.json" --receipt "<run>/create-receipt.json"
```

只有回执业务成功并有一致orderId，才能告诉用户订单已创建。显示原始官方支付链接及期限；链接缺失时指向麦当劳App订单页，不拼造链接。金额与确认不同时说明订单已创建、需核实，不能自动重下单。

## 付款后查取餐信息

用户说“付好了”或询问订单时，只查询一次：

```text
python -X utf8 "<skill>/scripts/mcmeal.py" meal query --state "<run>/meal.json"
```

调用输出的宿主`query-order`，保存`{"mode":"live","query_id":"本次id","arguments":{"orderId":"实际id"},"response":原始返回}`，再运行`meal status --state ... --receipt ...`。

只有官方orderStatus为已支付/配餐/完成等已知状态，才显示取餐码、取餐柜信息。用户说已付不等于支付已核实；待支付、已取消、未知状态或缺取餐码时如实说明。后台不轮询，用户再问才再查。

`meal bill --state ...`输出分账清单，付款前是预计金额；实付金额完整返回后按实际金额到分分摊。当前官方订单金额字符串按元解析，整数分报价来自calculate-price；无法解析则保持估算并明确说明。默认群文案不含支付链接、取餐密码或手机号。

## 超时或结果不明

创建订单的超时、工具报错、业务失败或缺少一致orderId都可能无法证明未提交。保持outcome_unknown，禁止重复confirm、重新preview或另开记录绕过状态。`meal query`先导出只读order-list请求。

让用户核对候选订单。只有用户确认这是刚才那单，且门店、商品/数量、可核实套餐子项、金额和时间匹配，才执行`meal reconcile --state ... --receipt ... --order-id ... --user-reply "确认认领订单"`，然后再query-order核实完整状态。无法确定时保留不明状态并说明证据缺口，不自动再下单。锁文件存在时不得并行操作或删除锁来绕过未解决状态。

## 复购与无账号试用

`meal repeat --state ...`读取上次人数、偏好和餐品，返回needs_current_menu；不自动提交。按用户本次修改，查询当前门店和供应、重新核价、用新记录预览确认，旧预约时间不沿用。

`demo --out ...`包含完整虚构购买演练。demo confirm只返回simulation_request，从不返回真实create-order工具请求。生成的订单、链接和取餐码都不可用于实际购买；全程无网络、零真实订单。
