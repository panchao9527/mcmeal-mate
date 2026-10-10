# 文件数据契约

这些文件由AI助手准备，用户只需表达需求。不要让用户手写JSON。运行数据放在用户工作目录的临时子目录；不要写回安装包或提交Git。

## session.json

- `mode`：真实调用为`live`；`demo`仅用于明确标注的虚构演示。
- `context`：复制当前工具参数中的`storeCode`、`orderType:1`、`beType:1`，可加`reservationDate`及`needTableware`。当前确定性流程限到店自取。
- `store_name`：所选官方门店名称。
- `request`：`budget_cents`、1至8位`participants`、可选`shared`、可选`payer_id`。稳定成员id用于修改需求与分账，不使用手机号。
- `candidates`：1至40个本次查询并独立试算过的候选餐。一人会分到一个完整候选，因此候选可由多个单点商品组成，也可是一份已指定子项的套餐。
- `fee_cents`：可选的已知额外整单费用，默认0。独立候选报价只是估算；最终必须整单核价。

成员字段：`id/name`，`required_categories`（如`main/drink/fries`），`prefer_tags`（软偏好），`exclude_tags`（硬排除项），可选整数`max_kcal`（由用户指定，含均分小食）。

共享项：`offer_id`、正整数`quantity`、`participant_ids`。被分摊的人必须也是食用成员；不能仅为凑预算漏掉人。

每个候选：

```json
{
  "id": "candidate-a",
  "name": "从本次结果得到的餐品组合名称",
  "categories": ["main", "drink"],
  "tags": ["grilled"],
  "known_tags": ["beef", "sugary-drink"],
  "attribute_evidence": {
    "beef": "当前菜单分类及餐品详情的主食肉类依据；仅口味分类",
    "sugary-drink": "所选饮料的官方名称或明确属性"
  },
  "kcal": null,
  "available": true,
  "items": [{"productCode": "从当前工具结果复制", "quantity": 1}],
  "quote": {
    "arguments": {"storeCode": "实际门店编码", "orderType": 1, "beType": 1, "items": [{"productCode": "从当前工具结果复制", "quantity": 1}]},
    "response": {"success": true, "datetime": "从官方返回原样保留", "data": {"price": 0}}
  }
}
```

上例只说明结构，禁止把其中占位符或0元价格当实际结果。`quote.response`应保存宿主调用的完整原始返回，而不是手填摘要。支持业务JSON、`structuredContent`、单个JSON文本`content`以及JSON-RPC `result`外层。

`tags`是确认命中的属性；`known_tags`是已确认存在或不存在的属性。未知属性不放入known_tags。不能因为名称未出现某配料就断言不含该配料。`attribute_evidence`为每个known_tag记录实际依据。严格配料、过敏或疾病需求须另向官方核实，不能由这种口味分类保证。

`kcal`不为null时必须有`nutrition_evidence`，记录官方营养工具的准确品名、规格及相加过程；任何子项缺失则保留null。脚本校验字段完整性，不能替AI判断引用是否真实。

`items`保存当前工具schema要求的`productCode/quantity/roundList/modification/couponId/couponCode`。轮数不固定，每个`roundList`成员保存`round`和`comboItemList`。不同饮料、子项、特制或券参数不会合并。

用券时，候选另带`coupon:{id,owner,max_uses}`；`id`必须与items中的couponId一致，owner必须等于request.payer_id。仅在用户要求用券、所属账号和规则确已核实时使用。一个候选当前支持一份券资源；多券叠加先通过宿主核实，不能绕开次数校验。

## draft.json 与 receipt.json

`prepare`输出preference/economy两套方案。可行方案的`quote_request`含真实调用参数和`plan_fingerprint`；不可行方案保留具体原因。

宿主AI客户端调用`calculate-price`后，保存：

```json
{
  "plan_fingerprint": "复制该方案生成的指纹",
  "arguments": {"storeCode": "实际门店编码", "orderType": 1, "beType": 1, "items": []},
  "response": {"原样保存": "本次宿主MCP工具的完整返回"}
}
```

`arguments`必须是此次实际调用所用的参数。finalize比对门店、商品、数量、套餐选择及方案指纹，检查工具错误、业务success、整数分价格和报价时间。真实报价超过15分钟、早于当前方案或发生参数错配时拒绝。指纹用于防止误用旧文件，不是官方数字签名或防伪证明。

同一draft内两种策略若调用参数完全相同，可只调用一次，分别用对应指纹封装同一次返回。修改需求后重新normalize/prepare，再调用官方核价；不要给旧返回换上新指纹冒充新调用。
