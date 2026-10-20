# dev-workflow：从这里开始

把需求对齐、前后端开发、验证和进度记录放进一个 Skill。Codex / Claude Code 共用，内置 8 个模块，不用逐个安装。当前为开发者测试版；无人值守能力取决于实际模型与项目环境。

## 最省事：让 AI 安装，然后描述目标

把解压后的目录交给 Codex 或 Claude Code，说：

> 请读取这个目录的 README.md，安装 dev-workflow 到当前 harness，检查安装结果。保留已有不同版本，遇到冲突先给出备份和替换方案。不要修改我的模型或权限配置。

安装后，在项目里说：

> 使用 dev-workflow，帮我完成【目标】。按项目规模对齐需求，拆出具体任务和验收条件，然后持续开发和验证。

AI 默认在当前对话里工作：小修复用短计划，完整功能和项目改造深入访谈并显示具体功能节点。你不需要填写 JSON，也不需要启动服务器。独立运行所需的配置、模块拆解和验收由 AI 准备；需求确认仍来自你的真实回答。

前端默认使用 [frontend](references/frontend.md)：沿用项目技术栈，明确组件、接口和交互状态，保留响应式与可访问性验证；配色、字体、布局、素材和动效由 AI 按项目判断。已有明确答案不重复问，不要求每次先做风格投票、方案册或设计引擎初始化。你点名某套设计方法或确实需要专用工具时，再选 Taste、Impeccable 或综合审查。

要按功能逐个交付远程 PR，可以说：“用功能子 PR 模式开发，按复杂度、依赖和风险拆任务，运行中发现任务过大可在原目标内细分。”AI 会准备拆分依据、固定检查、预算和协调者配置。子功能逐个验收后，原任务仍检查完整结果；详见 [任务拆分](references/task-partitioning.md)。

## 手动安装（可选）

需要 Python 3.10+；控制器还需要 Git 和已登录的原生 CLI。在解压后的 `dev-workflow` 目录打开终端，选一条：

```text
python scripts/workflow_portable.py install --harness codex --user
python scripts/workflow_portable.py install --harness claude --user
```

不要两条都执行，除非你两边都用。Codex 新安装进入 `~/.agents/skills/dev-workflow`，Claude Code 进入 `~/.claude/skills/dev-workflow`。已有不同版本会保留并提示处理；发现旧 Codex 安装路径时，安装器会停止并提示由 AI 备份后升级原位置，避免重复入口。安装后若当前会话未发现技能，重新进入一个会话。

安装位置依据 [Codex 官方说明](https://learn.chatgpt.com/docs/customization/overview) 和 [Claude Code 官方说明](https://code.claude.com/docs/en/skills)。详细迁移和已有版本处理见 [portability.md](references/portability.md)。

## 先跑一个不消耗模型额度的小演示

以下从包目录执行；把路径改为你的实际目录，空格路径加引号。输出目录必须是新的，不能位于 Skill 内：

```text
python scripts/workflow.py self-test --output ../workflow-demo
```

程序创建隔离示例仓库，模拟工作者修复函数，经 4 个实际行为检查后提交到工作分支，输出报告路径。它检验本机控制流程，**没有调用真实模型**，也不修改你的项目。目录里保留演示代码、日志和报告。

## 需要夜间或多分支自动运行时

普通对话触发后，AI 会按 [会话流程图](references/session-view.md) 登记实际计划并打开本机预览；动作变化时同步记录。Python 或预览环境不可用时会说明回退到聊天进度。

独立控制器服务启动后可在浏览器打开图形化运行看板：用节点和连线看进度与依赖，点击节点再查看分支、失败尝试和日志，详见 [看板使用说明](references/observability.md)。安装本包不会自动启动服务。

把下面这段交给 AI 即可：

> 使用 dev-workflow 为【项目路径】准备独立自动运行。目标是【目标】，先复用我们已确认的需求，默认交付分支提交和验收报告。请替我准备配置、计划和验收，实际验证模型和文件工具可用，说明预算后再按授权执行。

AI 使用的准备命令如下；日常轻量开发无需执行：

```text
python scripts/workflow.py setup --project ../my-project --harness codex
python scripts/workflow.py doctor --project ../my-project --runtime ../.workflow-my-project/runtime.json
python scripts/workflow.py doctor --project ../my-project --runtime ../.workflow-my-project/runtime.json --live --output ../workflow-probe
```

Claude Code 把 setup 的 harness 改成 claude；多分支加 `--mode full`，一次生成 worker、reviewer 和 coordinator。setup 会生成 START.md、运行配置和项目信息，保存在项目旁边；它不创建需求确认、不改业务代码、不自动派工。

doctor 默认只检查包、Python、Git、CLI 和项目基线。`--live` **会调用模型、消耗额度**，默认每种不同角色配置最多 90 秒；相同配置共用探针。探针通过仍不代表浏览器、数据库或项目测试通过。这三个新命令默认显示简短中文结果；脚本调用时加 `--json` 获取结构化结果。

| 检查结果 | 接下来做什么 |
|---|---|
| 找不到 CLI，或 PATH 指向 .cmd/.ps1 | 安装并登录 CLI，或让 AI 找到原生程序后传 `--executable`；不放宽权限 |
| model_unavailable / authentication / account_quota | 使用该账户实际可用的模型或完成登录/额度处理，再做探针；不自动改你的模型配置 |
| no_progress / time_limit / 缺文件回执 | 查看探针目录里的进程记录；文件工具或模型响应尚未验证，不开始无人值守业务任务 |
| Git 无提交 / 有未提交修改 | 由 AI 说明如何保留现有工作、准备基线；不要丢弃修改 |
| 离线通过，live 未检查 | 安装与程序存在已检查；模型实际调用仍未验证 |

自动运行建议用 `setup --model 已验证的模型ID` 固定本次模型；不指定则沿用宿主配置，宿主变更可能影响后续阶段。高级用法仅在需要时读 [统一入口](references/entrypoint.md)、[分支协作](references/coordination.md) 或 [服务器部署](references/service.md)。

## 语言 / Language

需要逐功能自动 PR 时，可直接告诉 AI：“使用 dev-workflow，先讨论项目的改进方向，再把确认的功能逐个开发、测试和创建子 PR，合并到本次专属工作分支；合并后回归失败就普通 revert 并修复重试，最后交付草稿主 PR。”AI 负责调查远程仓库、准备固定测试、预算与授权记录。首次支持 GitHub，功能串行验收，最终不自动合并到项目 main。详见 [功能子 PR](references/feature-prs.md)。

默认英文；本机可选中文。首次使用时告诉 AI “这套工作流使用中文”，或运行 `python scripts/workflow_locale.py --set zh-CN`。看板右上角切换并记住浏览器语言；任务输出沿用本地偏好或计划的 `locale`。[完整规则](references/language.md)。个人偏好不打进安装包。


## 记录真实耗时与用量

`python scripts/workflow.py metrics --run-dir RUN` 只读汇总 worker、reviewer、coordinator 的进程耗时、SDK usage 与缺失覆盖率；不会调用模型。中断可能没有完整 usage，未知费用保留 null。缓存输入、推理输出分别保留，不重复加到总 token。真实账单与宿主聊天用量不能从这些日志取得；实际用户决定、宿主救援和计划故障演练需另写操作记录。详见 [metrics.md](references/metrics.md)。
