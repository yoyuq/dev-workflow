# dev-workflow

[English](README.md) · [详细使用说明](dev-workflow/README.md) · [许可证](LICENSE)

适用于 **Codex 和 Claude Code** 的统一开发工作流。安装一个 Skill，描述目标，由 AI 先对齐需求、拆模块和任务、写执行计划，再实施、验证，并根据结果继续推进。

当前版本 **2.18.0，开发预览版**。包含八个任务技能模块，以及可选的 Python 总控、独立执行服务和动态流程图。

默认入口只保留对齐目标、相称的计划、实施和验证；复杂自动化按需展开。前端先读[简短工程指南](dev-workflow/references/frontend.md)，由 AI 根据需求、内容和现有项目决定视觉方向。Taste、Impeccable 保留为可选工具，普通界面开发不再默认套用主题、字体、布局参数或设计引擎流程。

独立工作者的任务卡直接提供小型完整指南（总内容上限 24 KiB），减少启动时的文件读取往返；大指南仍在磁盘按需读取，不截断内容。普通会话批量调查相关入口，复用未变代码的已通过检查；固定门禁、独立验收和合并后回归保持原要求。

## 能做什么

- 新目标先用内置 `grill-me` 确认影响交付的需求，按任务规模追问。
- 把目标拆成可验收的任务，明确依赖和验证方法，再写计划。
- 按阶段调用前端、后端、PRD 和测试等模块，无需逐个安装。
- 在动态图中展示实际节点、当前动作、尝试、事件和日志。
- 总控管理不同 Git worktree 中的工作分支；工作者通过消息传递进度、依赖和问题；full 模式要求独立审阅。
- 可选[功能子 PR 交付](dev-workflow/references/feature-prs.md)：逐功能测试、独立审阅、合并到本次工作分支，并测试实际远端合并；失败普通 revert 后在原预算内修复重试。最终工作分支只创建草稿 PR。首版支持 GitHub，串行验收功能。
- [按功能拆分任务](dev-workflow/references/task-partitioning.md)：结合复杂度、依赖和风险划分分支；运行中可由 AI 协调者细分原功能，保留原任务完整验收和预算；强原子改动可有依据地保留。
- 保存检查点、失败证据和候选经验，经过验证的经验才进入可复用记录。
- 支持独立进程或服务器持续推进任务；需要人工决定时等待真实答复，再接续运行。
- 可选[额度恢复](dev-workflow/references/provider-recovery.md)：在预先配置的入口间切换；全部受限时持久等待，到期自动继续原任务和 PR，保留原验收、独立审阅、合并回归与预算。

## 安装

需要支持文件和命令工具的 AI 编程环境，以及 **Python 3.10+**。独立执行还需要 Git、项目依赖和已安装并登录的 Codex 或 Claude Code CLI。看板测试和部分可选模块工具需要 Node.js。

```sh
git clone https://github.com/yoyuq/dev-workflow.git
cd dev-workflow
python dev-workflow/scripts/workflow_portable.py doctor
```

按实际使用的环境安装，也可以两个都安装：

```sh
python dev-workflow/scripts/workflow_portable.py install --harness codex --user
python dev-workflow/scripts/workflow_portable.py install --harness claude --user
```

全新 Codex 安装进入 `~/.agents/skills/dev-workflow`，Claude Code 进入 `~/.claude/skills/dev-workflow`。安装器不会覆盖内容不同的旧版本；如果已有 `~/.codex/skills/dev-workflow`，让 AI 先备份，再明确升级原位置，避免重复安装。

然后告诉 AI：

> 使用 dev-workflow 实现这个目标：……。先与我对齐需求，拆成模块和可验收任务，写执行计划并展示进度图，再持续推进到满足约定的验收条件。

Codex 可以显式输入 `$dev-workflow`，Claude Code 使用 `/dev-workflow`。默认在当前会话中实施；看图不会额外启动模型。图只展示 AI 实际登记的公开进度，AI 不更新记录时，图也不会自行变化。

## 语言

通用默认英文；用户明确选择语言后按其选择输出。看板右上角可切换英文/简体中文。新任务的本机 AI 输出要默认中文，可以运行：

```sh
python dev-workflow/scripts/workflow_locale.py --set zh-CN
```

个人偏好、令牌和运行数据不进入技能包。当前大部分指南，以及 `setup`、`doctor`、`self-test` 的简短命令行摘要仍是中文。自定义任务内容需要 AI 准备匹配原文的展示译文；日志和验收证据保留原文，详见 [语言规则](dev-workflow/references/language.md)。

## 独立运行与服务器

```sh
python dev-workflow/scripts/workflow.py setup --project ../my-project --harness codex
python dev-workflow/scripts/workflow.py doctor --project ../my-project --runtime ../.workflow-my-project/runtime.json
```

Claude Code 改为 `--harness claude`；多任务协作在 setup 时加 `--mode full`，并配置独立 reviewer。setup 只准备配置，实际任务仍需确认需求、准备计划和验收。doctor 默认离线；加 `--live --output ../workflow-probe` 才调用真实模型并消耗额度。

[独立服务器服务](dev-workflow/references/service.md) 根据队列、执行结果和人工答复推进工作，不是按定时器反复重跑固定任务。仓库提供部署示例；服务器、CLI 登录和项目依赖由使用者配置。

## 离线体验与验证

```sh
python dev-workflow/scripts/workflow.py self-test --output ../workflow-demo
```

离线演示使用模拟模型工作者，并实际操作隔离的 Git 测试仓库；不会调用模型或修改你的项目，不能代替真实项目验收。

完整测试命令见 [英文 README](README.md#try-and-verify)。2.12.2 原开发基线为 295 个 Python 测试通过、1 个 Windows 符号链接权限用例跳过，看板测试通过。仓库配置了 Linux/Windows 的 GitHub Actions；是否通过以实际运行结果为准。

## 文档与边界

[完整入口](dev-workflow/SKILL.md) · [模块路由](dev-workflow/references/skill-routing.md) · [规划细度](dev-workflow/references/plan-depth.md) · [多分支协作](dev-workflow/references/coordination.md) · [受控重规划](dev-workflow/references/replanning.md) · [经验记录](dev-workflow/references/learning-loop.md) · [故障排查](dev-workflow/references/observability.md)

这是一个 Skill 和配套工具、控制器/服务，当前不是 MCP Server。重规划保持已确认的目标、范围、验收和预算；默认交付分支提交与报告，推送、合并和部署遵循真实用户授权。独立控制器不代替操作系统隔离；模型账单上限仍由提供商管理。真实模型、浏览器/数据库和云部署需要按项目验证。

## 开源许可与贡献

原工作流代码、文档和作者自定义模块使用 [MIT](LICENSE)。内置第三方模块保留原有 MIT/Apache-2.0 许可和署名，见 [NOTICE](NOTICE.md)、[来源记录](dev-workflow/THIRD_PARTY.md) 与 [许可证全文](dev-workflow/licenses/)。第三方内容是保留的本地快照，未确定对应的精确上游 commit；本项目与这些作者、OpenAI 和 Anthropic 无隶属关系。

欢迎提交可复现的 Issue 或 PR，要求见 [CONTRIBUTING.md](CONTRIBUTING.md)。不要在问题报告中附令牌、密钥或私人项目数据。


## 2.13 的真实运行指标与审阅修复

`python dev-workflow/scripts/workflow.py metrics --run-dir RUN` 只读汇总模型进程耗时与 SDK 用量，并明确中断造成的缺失；未知账单、宿主救援不按零处理。详情见 [metrics.md](dev-workflow/references/metrics.md)。只读审阅的成功工具完成可更新空闲计时，思考/心跳/失败/重复事件不算进度，实现者仍使用产物看护，总预算与验收保持。


## 2.14 自动检查与有限恢复

开工前由 AI 把共享配置/启动与实际运行交付测试等风险检查固定到原任务负责人；失败按原预算返工。明确归属的最终补充检查失败可有限重开负责人，修复后重新完整验收和整分支审阅。审阅进程可按预设次数另起上下文恢复；认证/权限、输入/源码漂移及无效报告不放行，预检也受剩余时间限制并持久记账。详情见 [自动检查与有限恢复](dev-workflow/references/automatic-assurance.md)。这是风险检查闭环，不保证自动发现所有未知问题。

2.14.1 还区分已观察到的 SDK 内部重连与终止失败：只有有效成功轮次能解除临时断流标志；认证/权限、其他错误或不完整完成仍阻塞，避免已恢复的连接造成多余返工。历史实跑继续使用原冻结快照。

本机看板关闭过程中截断 HTTP 响应时，状态探测现在返回不可用，避免未捕获异常；真实截断响应回归检查会核对会话数据保持。
