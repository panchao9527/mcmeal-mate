# 安装麦麦搭子 Skill

安装后直接在AI客户端聊天，不需要启动网站。客户端负责理解需求和调用麦当劳MCP；Python助手管理配餐、购买确认、订单回执和分账。需要支持本地Skill、MCP工具与Python 3.10+脚本执行的客户端。

## 选择一种安装方式

### WorkBuddy：导入本地技能包

1. 下载项目提供的 `mcmeal-mate-skill.zip` 分发包，或者按本文末尾命令自行生成。
2. 在 WorkBuddy 的技能页面选择 **添加技能 → 上传技能**，选择本地 ZIP。
3. 在已安装列表确认 `mcmeal-mate` 已启用，然后新建对话。

此入口依据 [WorkBuddy 官方技能说明](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)（核对日期：2026-10-10）。不同版本界面可能不同，以客户端显示为准。该说明确认了本地技能包导入；本项目已验证便携包结构和脚本运行，尚未完成 WorkBuddy 图形界面的实际导入验收，也未声称上架 WorkBuddy 技能市场。

默认 ZIP 包含 `mcmeal-mate/SKILL.md` 及其资源。如果导入器明确要求 ZIP 根目录直接是 `SKILL.md`，可使用 `--flat` 生成对应布局。不要把整个开发仓库压缩后导入。

### Codex：项目内安装

使用下载的ZIP时：解压后，将完整的 `mcmeal-mate` 文件夹复制到目标项目的 `.agents/skills/` 中，最终路径应是 `.agents/skills/mcmeal-mate/SKILL.md`。然后在该项目新建对话，选择或调用 `mcmeal-mate`。

如果你下载的是**完整源码仓库**，也可以在源码目录运行下面的安装脚本（ZIP包不包含这个开发脚本）：

```powershell
python -X utf8 scripts/install_skill.py --dest .agents/skills
```

脚本将技能安装到当前项目的 `.agents/skills/mcmeal-mate/`。在这个项目中开启 Codex 新对话，使用技能选择器选中 `mcmeal-mate`，或输入“使用 mcmeal-mate 帮我配餐”。Codex CLI / IDE 中也可输入 `$mcmeal-mate`。

希望跨项目使用时，可把解压后的完整技能文件夹复制到个人 `~/.agents/skills/` 目录。使用完整源码仓库时，也可在PowerShell中运行：

```powershell
python -X utf8 scripts/install_skill.py --dest "$HOME/.agents/skills"
```

这是你主动执行时的安装选项，构建和测试本项目不会自动改写个人技能目录。官方当前记录的用户/项目目录均为 `.agents/skills`；参见 [OpenAI 官方 Build skills](https://learn.chatgpt.com/docs/build-skills)（核对日期：2026-10-10）。新技能通常会被自动识别，未出现时重启客户端。

### 其他支持本地 Skill 的客户端：手动复制

解压 ZIP，把完整的 `mcmeal-mate` 文件夹放进该客户端文档指定的 Skills 目录。保持 `SKILL.md`、`references/`、`scripts/`、`mcmeal/`、`examples/` 的相对位置。不要只复制 `SKILL.md`。

客户端必须能读取技能文件并执行附带的 Python 脚本。只有普通聊天、无法执行脚本的客户端不在本包的完整配餐验收范围内。

## 第一次使用

先发送：

> 使用麦麦搭子，检查技能、Python和麦当劳MCP工具是否可用。没连接时先做一次无账号买餐演练，全部标明虚构，不调用真实下单。

离线演示可以直接使用，无需 Token。实时配餐需要在当前客户端连接 [麦当劳官方 MCP](https://open.mcd.cn/mcp)。Token 只输入客户端的认证配置，不要发到对话里，也不需要写进本技能。已有连接时直接复用，安装脚本不会再索要 Token。

确认连接后，说出需求：

> 用麦麦搭子帮我们3人买餐，上海人民广场附近外带，预算100元。小王主食不要牛肉，小李饮料必须无糖，我想吃汉堡，再共享一份薯条。买之前让我看清单确认。

AI确定门店、查询菜单并核价。后续说“预算改成80元”“小李不参加了”即可保留其他条件重新安排。选方案后，AI展示包含门店、份数、规格、特制和金额的购买清单。你回复“确认下单”后才再核价并创建待支付订单；付款由你在官方页面完成。付款后说“付好了，查取餐码”，AI查询状态并给实际分账。

购买需要当前连接提供`create-order`与`query-order`，未知下单结果的核实需要`order-list`。缺少时只能先配餐，不能宣称订单已经创建。v0.4已验证离线购买演练并核对官方工具schema；真实创建订单、付款和取餐尚未实测。

安装成功只说明文件已经就位。首次检查要看到客户端实际读取了本技能，脚本可以执行；实时模式还要看到麦当劳查询工具可用和实际报价成功。

## 更新与卸载

安装脚本遇到同名技能会停止，保留已有文件。更新前先将旧 `mcmeal-mate` 文件夹备份到 Skills 目录以外，再导入新版本。若通过 WorkBuddy 导入，优先使用客户端提供的技能管理操作。

手动安装的技能可通过删除你安装的 `mcmeal-mate` 文件夹卸载；这不会删除或修改客户端的 MCP 连接。不要删除整个 Skills 父目录。

## 从源码生成分发包

以下命令在完整源码仓库中执行；便携技能包不包含开发用的安装/打包脚本。需要 Python 3.10+，不需要安装第三方依赖：

```powershell
python -X utf8 scripts/package_skill.py
```

产物是 `dist/mcmeal-mate-skill.zip`。如需根目录布局：

```powershell
python -X utf8 scripts/package_skill.py --flat --out dist/mcmeal-mate-skill-flat.zip
```

脚本仅复制明确列出的技能入口、参考资料、离线运行代码和公开示例，并附文件 SHA-256 清单。不会递归复制仓库，不包含网页、`.git`、测试、个人配置、Token、`local-runs` 或真实调用原始日志。它不会读取你的凭证，不会连接 MCP，也不会自动安装到任何客户端。输出文件已存在时停止，避免意外覆盖旧包。
