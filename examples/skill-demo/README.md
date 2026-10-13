# 无账号演练

`session.json` 中的门店、餐品编码、营养和金额均为虚构，专用于离线验证。不得将其编码发送给麦当劳服务，不得作为真实点餐建议。

在任意工作目录运行：

```text
python /安装位置/mcmeal-mate/scripts/mcmeal.py demo --out ./mcmeal-demo
```

会生成两种配餐方案、绑定方案的模拟核价回执和分享清单，还会生成完整买餐演练：`buying-transcript.md`、`meal.json`、`buying-receipts.json`。它包含购买确认、模拟待支付订单、模拟支付状态查询、演示取餐码和分账。所有订单、链接和取餐码均为虚构，不可付款或取餐。

全程无需Token、MCP或网站，零网络、零真实订单。demo确认只导出simulation_request，禁止把它交给真实create-order。

真实使用时，AI 客户端根据本次麦当劳 MCP 查询结果构建同样的数据结构；字段说明见 `references/input-schema.md`。
