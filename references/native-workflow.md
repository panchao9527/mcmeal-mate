# 宿主MCP对话流程

以下动作由承载Skill的AI助手执行，用户通过自然语言提供需求。脚本以绝对路径调用，当前工作目录可以任意。`<skill>`指本SKILL.md所在目录，`<run>`指用户工作目录中新建的一次性运行目录。

1. 查看客户端已连接的麦当劳MCP工具及最新schema。工具显示名可能带服务器前缀，按实际发现结果调用。不存在连接时指引用户按 [安装说明](../docs/INSTALL.md) 配置；不要启动网页或要求在聊天中贴Token。
2. 读懂人数、总预算、门店/地点、到店自取需求及个人限制。只补问影响正确性的缺项。查`query-nearby-stores`后让用户从真实营业门店中选择；保留同一饭局已经确认的选择。
3. 调`query-meals`获取当前菜单；为需求挑选有界候选，调`query-meal-detail`查看真实子项。商品编码来自当前返回，不使用示例编码或旧网页的固定商品清单。早餐、正餐和套餐轮数以结果为准。
4. 必要时调`list-nutrition-foods`；精确匹配品名和规格。肉类、辣味、饮料等属性按明确菜单事实分类，未知留空。带过敏、疾病等要求时不得以此代替专业核实。
5. 对候选调用宿主`calculate-price`，保留参数和原始返回，构建`session.json`。有券需求时才查`query-store-coupons`并核实归属、可用次数和适用条件。未获授权的领券、购卡、积分兑换和下单不属于本流程。
6. 运行本地计算，所有脚本均离线，不需要MCD_MCP_TOKEN：

```text
python -X utf8 "<skill>/scripts/mcmeal.py" normalize --input "<run>/session.json" --out "<run>/normalized.json"
python -X utf8 "<skill>/scripts/mcmeal.py" prepare --input "<run>/normalized.json" --out "<run>/draft.json"
```

7. 读取draft的两套结果，说明估算与软偏好取舍。无解时保留硬性需求，可扩充候选、核实整单优惠或请用户选择调整项。不能把有界候选无解推成整家门店都无解。
8. 将每套可行方案的`quote_request.arguments`原样交给宿主`calculate-price`。保存`receipt.json`（格式见[input-schema.md](input-schema.md)），然后：

```text
python -X utf8 "<skill>/scripts/mcmeal.py" finalize --draft "<run>/draft.json" --receipt "<run>/preference-receipt.json" --strategy preference --out "<run>/preference-final.json"
python -X utf8 "<skill>/scripts/mcmeal.py" share --input "<run>/preference-final.json" --out "<run>/preference-share.md"
```

另一策略使用economy。官方总价超预算时为quote_over_budget，明确显示未通过，不让用户把它当可采用方案。

9. 在聊天中直接交付两种方案对比、每人餐品与金额、门店和核价时间、未满足的软偏好，再附可复制的群清单。不让用户自己打开JSON找答案。用户改预算或偏好时保留其他条件，重新规划和整单核价。

## 首次运行检查

```text
python -X utf8 "<skill>/scripts/mcmeal.py" doctor
python -X utf8 "<skill>/scripts/mcmeal.py" demo --out "<run>/offline-demo"
```

doctor只检查离线助手可运行，不代表宿主MCP已连接。demo使用虚构数据，分享结果必须保留“离线演示”标签。没有Python时说明需要Python 3.10+；不要宣称已经运行约束搜索。
