# 麦麦搭子 McMealMate Skill

**给 AI 助手装一个技能，让它把一顿多人麦当劳安排明白。**

> 上海人民广场附近，我们3个人吃麦当劳，预算100元。小王主食不要牛肉，小李饮料无糖，我想吃汉堡和薯条。给我两套方案，每人多少钱也算清楚。

麦麦搭子会在聊天里理解每个人的要求，查询你选择的门店、当前餐品和官方价格，给出“照顾口味”“候选内省钱”两套方案，以及能直接复制到群里的点餐和分账清单。

**v0.3 的主入口是 Skill 对话。** 复用 AI 客户端已经连接的麦当劳 MCP；附带的 Python 助手只做离线计算和校验，不读取 Token、不联网，也不需要启动网页服务。

## 三步使用

1. **安装技能包**：[下载 mcmeal-mate-skill.zip](https://github.com/panchao9527/mcmeal-mate/raw/refs/heads/main/dist/mcmeal-mate-skill.zip)。WorkBuddy 中按“技能 → 添加技能 → 上传技能”导入；其他客户端见[安装教程](docs/INSTALL.md)。客户端需支持本地 Skill、MCP 与 Python 3.10+ 执行。
2. **连接一次官方 MCP**：在客户端配置 [麦当劳官方 MCP](https://open.mcd.cn/mcp)。已有连接就直接复用；Token只放客户端设置，技能不会再要求把它写入脚本。
3. **直接说需求**：输入上面的例子。AI补齐必要信息、让你选择门店，随后返回已核价的两套方案和群清单。

后续可以直接说：

- “预算改成80元，其他要求不变。”
- “小李不参加了，重新安排。”
- “把省钱方案整理成可以发群里的文字。”

第一次还没有账号？先说 **“使用麦麦搭子，做一次无账号离线演示”**。包内附有明确标为虚构的示例，可验证安装与计算流程；不能把演示金额当成实际售价。

## 你会得到什么

- **每个人都有个人餐**：1至8人，支持主食类别、饮料、薯条等硬性要求，以及口味软偏好。
- **两套可比较的方案**：说明价格、预算余额与放弃的软偏好，不宣称全菜单最低价。
- **当前官方整单核价**：候选先试算，最终完整商品清单再核价，附门店和报价时间。
- **算到分的分账**：共享小食只分给食用成员，整单价差按原金额比例分摊。
- **可以带走的结果**：聊天表格与群分享文案，包含每人餐品、金额、共享品名和数量。
- **清楚的冲突反馈**：预算不足、当前不售卖、属性未知或旧报价不匹配时说明问题，保留用户的硬性要求。

## Skill 怎样工作

```text
用户自然语言
  → AI客户端调用已连接的麦当劳MCP
  → 查询门店、当前菜单、详情、营养和候选报价
  → 离线助手计算两种配餐方案
  → AI客户端按导出的准确参数调用官方整单核价
  → 校验业务结果、方案指纹、报价时间与预算
  → 在聊天交付两套方案和可复制群清单
```

商品编码、套餐轮数、饮料替换均取自本次工具返回。Skill 主流程没有固定商品编码清单，能够处理新候选和不同轮数的套餐；这不代表自动遍历全部菜单。

候选、整单参数和报价通过方案指纹关联，修改预算、人数、门店或餐品后需重新核价。过期、失败、金额格式错误或错配的报价会被拒绝。指纹是文件一致性检查，不是官方数字签名。

当前确定性流程支持**到店自取**。有用户指定能量上限时参考官方标准份量；无法匹配的营养保留未知。肉类和口味分类不能保证过敏、交叉接触或严格配料安全，需要另向官方核实。普通流程只查询和试算。

## 安装与源码

- [完整安装和首次使用教程](docs/INSTALL.md)
- [技能入口 SKILL.md](SKILL.md)
- [宿主MCP执行流程](references/native-workflow.md)
- [候选、方案和报价的数据契约](references/input-schema.md)
- [MCP实际使用说明](MCP_INTEGRATION.md)
- [验证记录与适用边界](docs/VALIDATION.md)

开发者可从源码运行同一个离线助手：

```bash
python -X utf8 scripts/mcmeal.py doctor
python -X utf8 scripts/mcmeal.py demo --out local-runs/skill-demo
python -X utf8 -m unittest discover -s tests -v
python -X utf8 scripts/package_skill.py
```

安装包只包含 Skill、参考文档、离线代码和虚构示例。不会打包网页、旧直连客户端、测试、真实运行日志或凭证。也提供[根目录布局的ZIP](https://github.com/panchao9527/mcmeal-mate/raw/refs/heads/main/dist/mcmeal-mate-skill-flat.zip)，供明确要求根目录含`SKILL.md`的导入器使用。

此前的网页和直连命令仍作为开发时的[可选可视化工作台](docs/optional-web-demo.md)保留，不进入 Skill 分发包。无须安装或启动它们即可运行主流程。

## 验证与参赛

已验证离线完整流程、异目录安装后运行、旧回执拒绝、新商品编码及不同套餐轮数、业务失败和金额校验、分账守恒。真实官方接口与客户端导入的验证范围分别记录在验证报告中；尚未把 WorkBuddy 图形界面导入或所有客户端兼容性表述为已实测。

项目参加 [麦当劳程序员创意开发大赛](https://github.com/M-China/mcd-developer-innovation-challenge)，[报名申请 #82](https://github.com/M-China/mcd-developer-innovation-challenge/issues/82) 已收到[官方成功参赛回复](https://github.com/M-China/mcd-developer-innovation-challenge/issues/82#issuecomment-6079533897)。本仓库持续更新同一个项目。

社区原创作品，使用AI编程辅助开发，非麦当劳官方产品。当前开发使用Codex，未申报WorkBuddy专项开发奖励。原创代码采用[MIT License](LICENSE)，[官方参赛声明](CONTEST_DECLARATION.md)保持原文；官方服务、数据和商标权利不因本项目许可证改变。
