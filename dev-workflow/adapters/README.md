# Harness 适配

先读当前平台：[Codex](codex.md) / [Claude Code](claude-code.md)。核心工作流只依赖能力，不依赖某个工具拼写。

| 能力 | 适配方法 |
| --- | --- |
| 读取指南、项目、状态 | 宿主文件读取工具；以实际 Skill 根解析相对路径 |
| 修改项目和状态 | 宿主编辑工具或受控脚本；遵守项目规则 |
| 运行检查 | 命令执行工具，显式设置项目 cwd，记录退出码 |
| 必要澄清 | 当前宿主的用户输入能力或普通消息 |
| 浏览器、截图 | 已有浏览器工具或项目测试运行器；缺失时明确未验证 |
| 独立审查 | full 控制器启动独立 reviewer 进程；交互自审须声明为自审，不能替代要求的独立节点 |
| 跨会话记忆 | 项目文件；不依赖某个聊天平台的内置记忆 |

`python <WORKFLOW_ROOT>/scripts/workflow_portable.py doctor` 检测资源摘要和本机程序是否位于 PATH；它不验证账号、沙箱、浏览器连接或项目数据库。运行环境仍要用实际任务检查。

原生协调任务启动后，先以实际工作者文件工具完成 [消息预检](../references/worker-protocol.md)。默认 90 秒内收到 capability 声明和真实 assignment 回执；命令/浏览器的自述另行记录，不能当作已验证。工作者不能执行本地检查时，可明确交给控制器的固定外部验收。

无人值守调用用 [runner.md](../references/runner.md) 的运行预检和原生 CLI 适配器；版本探针会核对可执行文件摘要，发现安装中断、路径缺失或升级漂移即阻塞。模型认证只能通过实际调用核实。适配器保留统一计划、任务卡与验收规则，模型名称默认沿用宿主配置。

## 需要原始模块目录形状时

多数模块直接读 guide.md 即可。Impeccable 原始启动器等依赖 SKILL.md 文件名时，在技能发现目录之外创建任务副本：

```text
python <WORKFLOW_ROOT>/scripts/workflow_portable.py prepare-module impeccable --dest <任务运行目录>/impeccable
```

助手按宿主 shell 正确引用真实路径；尖括号仅为占位符。工具恢复原始文件名并保留相邻资源；目标已存在且内容不同会拒绝覆盖。

从副本用绝对路径调用原启动器，cwd 保持项目目录。原启动器可能需要网络获取其固定引擎，这不是本包安装过程的一部分。不可用时遵守原指南允许的降级流程；不能声称未运行的引擎或浏览器检查已完成。

ZIP 跨系统解压可能不保留执行位。Linux/macOS 可用 `sh <副本>/scripts/impeccable context`；Windows PowerShell 用 `& '<副本>\scripts\impeccable.cmd' context`。根据实际路径加引号，不依赖解压后的文件可执行权限。

内置代理配置是资源快照，不会自动生效。不要改写宿主配置以追求不同平台完全相同的工具行为。
