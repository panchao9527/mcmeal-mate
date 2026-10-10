# 无账号演练

`session.json` 中的门店、餐品编码、营养和金额均为虚构，专用于离线验证。不得将其编码发送给麦当劳服务，不得作为真实点餐建议。

在任意工作目录运行：

```text
python /安装位置/mcmeal-mate/scripts/mcmeal.py demo --out ./mcmeal-demo
```

会生成两种配餐方案、绑定方案的模拟核价回执和可复制的分享清单。每份结果明确标记“离线演示”。无需 Token、MCP 或网页服务。

真实使用时，AI 客户端根据本次麦当劳 MCP 查询结果构建同样的数据结构；字段说明见 `references/input-schema.md`。
