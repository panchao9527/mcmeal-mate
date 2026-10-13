# README对话示例图

`skill-demo.png`由`skill-demo.html`在浏览器中渲染。它是根据包内离线演练重新排版的对话示例，不是WorkBuddy、Codex或其他客户端的实测截图；不是已完成真实下单、付款或取餐的证据。

图中方案、金额、成员与取餐码读取自`examples/skill-demo/session.json`驱动的demo：90元预算，口味方案85元，省钱方案69元；3人各23元，包含各3元共享薯条。取餐码`DEMO-A01`不可用于真实取餐。图中用户表达是演示摘要，未使用真人聊天或私人订单。

在完整源码仓库中重新生成离线演练与HTML：

```powershell
python -X utf8 scripts/build_demo_example.py
```

浏览器截图使用独立Playwright会话；截图先存入`output/playwright/`，确认文字、金额和边界说明后复制到本目录的`skill-demo.png`。推荐1120像素宽，截取完整页面，保持中文字体可读。HTML不加载外部字体、图片或网络资源。

这些展示材料不进入Skill安装包，不是运行依赖。技能功能和已有ZIP无需为README图片重新打包。
