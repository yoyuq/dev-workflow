# 总控与分支协作

`workflow_coordinator.py` 是 Python 标准库控制器，复用包内 Codex/Claude CLI 适配器、进程看护、验收命令和错题本。它与串行 `workflow_runner.py` 并存，不把含合并提交的交付送进串行 v4 检查器。

## 准备与运行

先按 [grill-me 需求对齐](requirements-alignment.md) 获得确认，再完成模块拆解与执行计划书。复制 [协作 v2 计划模板](../assets/coordination-plan.example.json)，填入 requirements_alignment、requirements、实际绝对路径、完整基线提交和独立验收命令。初始化在创建分支、工作区和控制器目录前检查对齐记录与覆盖；续跑重新核对已固定的记录。选择已核实的基线；原仓库有未提交内容时明确是否纳入，不能暗自清理。例子是格式说明，不能原样执行。旧 v1 运行使用其冻结的 2.3 或更早技能，不修改旧 pins 就地升级。

新任务使用 [统一入口](entrypoint.md)。运行配置包含 worker、可选 coordinator，以及 full 模式必须提供的 reviewer；每个值采用 `assets/runtime.example.json` 的格式。推荐初次并发上限 2，各角色可选择同一种或不同 harness；不设置 model 时沿用本机配置。无 coordinator 时工作者间仍可通信，总控问题保留待处理。command 适配器只用于明确标注的模拟或可信集成。

需求深度与分支数量分别决定。项目级访谈和范围核对沿用 [需求对齐](requirements-alignment.md)，每个工作包的目标及只读计划写出具体产出、行为边界、依赖与验证，不将整个前端/后端当作一句目标。按可独立合并的结果拆分支；同分支内细步骤通过活动回报与消息说明。准备时核对完整范围，首条路径打通后仍推进其他承诺；当前图中的分支节点表示可调度工作包，不会伪装为每个内部动作都独立调度。

```text
python <WORKFLOW_ROOT>/scripts/workflow.py init --plan <plan.json> --runtime <runtime.json> --run-dir <new-run-dir> --mode full
python <WORKFLOW_ROOT>/scripts/workflow.py run --run-dir <run-dir>
python <WORKFLOW_ROOT>/scripts/workflow.py status --run-dir <run-dir>
```

控制器目录应在源仓库和 Skill 目录之外。初始化创建本次运行专用的 integration 分支，各任务/尝试与候选合并使用独立 worktree。默认交付本地分支与报告；用户要求并授权逐功能 GitHub PR 时使用 [feature_prs](feature-prs.md)，自动合并子 PR 到本次工作分支，回归通过再推进。原 checkout 的 HEAD 保持；项目基线分支只接收待审草稿 PR。初始化会在仓库 `info/exclude` 中排除 `.workflow-mailbox/`，避免消息进入提交。复制仓库用于演练时，不复制忽略的数据库、认证或硬件数据。

## 计划与依赖

- `goal`、`authorization_ref`、`execution_plan`、任务范围与验收固定摘要。每项检查至少引用一个仓库外的验收文件，控制器固定其摘要；`{repo}` 替换为当前被检查的工作目录。验收不得改变代码。
- 每个任务写 `module`、具体目标、允许路径、`verification` 和必要指南。保留简单的模块划分，不要求恰好前端/后端/测试三个任务。
- `allowed_scope` 明确列出的文件，即使被项目忽略，也纳入变更检查和提交；目录范围仍遵守忽略规则，不递归收集缓存。计划应逐个列出需要交付的被忽略文件，验收也应在全新工作区确认其存在。
- `depends_on` 指定必须先集成的任务。`produces` 声明唯一拥有者的里程碑，`requires` 消费已通过验收并集成的里程碑，环形依赖会被拒绝。
- 想在后端全部完成前释放前端，先拆出可验收的“接口约定”任务；本版不会把工作者随口报告或尚未提交的半成品当作可依赖里程碑。
- `resources` 是独占资源名，例如同一迁移集合、同一测试数据库或固定端口。总控不会同时启动占用相同资源的任务；端口和数据库的实际分配仍由项目准备步骤负责。
- `contracts` 为共享接口/决策文本。任务列明自己消费的约定。默认不可更改；显式 `editable: true` 才允许 AI 在既定需求内发布新版本。旧版本更新请求拒绝；受影响任务和后继任务失效，重新实施/验收，尝试预算不重置。
- 必须人工判断的事项写入 `manual_review`，并在需求验收中关联。该任务保留已通过自动检查的合并候选，停在 awaiting_review，不发布完成里程碑、不释放依赖。按 [人工答复与续跑](human-resume.md) 提交真实签收后继续，后续范围内代码变化会使签收失效。

## 需求覆盖与证据

`requirements` 是从已确认需求整理出的非空列表。每项有唯一 `id`、`text`、非空 `task_ids` 和 `acceptance`。每个任务必须关联需求，每条需求关联的每个任务都必须有对应验收证据。

验收项的 `id` 在计划内唯一，`text` 描述可观察结果。`type: automated` 的 `checks` 引用 `{task_id, check_id}`；检查必须属于该需求的任务，或 `task_id: integration` 引用最终组合检查。集成检查不能单独代替任务本身的覆盖。检查 ID 在各自任务/集成组内唯一。

`type: manual` 以 `task_id`、从 0 开始的 `review_index` 对应该任务 `manual_review` 中的项目；每个人工审阅项都要被映射。脚本不会把人工项目转成自动通过。最终 `delivery.json.requirements` 按需求列出任务、验收状态、自动检查日志与人工签收来源，全部通过才 completed。

覆盖校验只验证明确列出的需求及关联结构。它不能自动发现完全没写进列表的原始诉求，也不能证明一个测试真实覆盖其描述；准备时由宿主对照用户确认、实际测试行为与计划复核。

## 消息协议

SQLite 是总控的事实来源，`state.json`、`messages.json`、`events.json` 是可读导出，不是实时订阅。观察进度用统一入口 status 或服务 API。每个工作者只读自己的 `.workflow-mailbox/inbox.json`，使用文件工具或可选助手向自身 outbox 原子写入消息，总控根据实际工作目录确定发送者。原生工作者先完成 [文件能力预检](worker-protocol.md)，实现完成后用 ready_for_verification 交接；Python 助手被拒绝时无需更改权限。

```text
python <WORKFLOW_ROOT>/scripts/workflow_message.py --workspace . read --ack
python <WORKFLOW_ROOT>/scripts/workflow_message.py --workspace . post --type question --to BACKEND --text "分页字段采用哪个版本？"
python <WORKFLOW_ROOT>/scripts/workflow_message.py --workspace . post --type reply --to FRONTEND --reply-to <inbox-message-id> --text "根据契约 v2，使用 total；见对应源文件。"
python <WORKFLOW_ROOT>/scripts/workflow_message.py --workspace . post --type change_request --text "建议更新接口约定；原因、受影响消费者及兼容方式……"
python <WORKFLOW_ROOT>/scripts/workflow_message.py --workspace . wait --seconds 30 --ack
```

`--data-file` 可附 JSON 资料。其他类型有 progress、artifact、blocked；blocked 说明必要前置条件后结束本次尝试。只确认已读消息，不代表问题已解决。回复必须引用发给自己的原问题。重复投递去重，同 ID 改内容拒绝。消息体有大小和数量上限；超时或收件任务不活跃的问题转给总控。未解决请求会阻止 completed。

AI 总控在有事件时按固定上下文启动一次，写结构化 decision.json。允许 reply、retry、defer、prioritize、publish_contract，以及预先启用策略时的 [replan 提案](replanning.md)；不允许标记完成、改验收或扩大目标。状态、决策消息和请求确认在一个事务中保存。defer 保留必要人工问题，重试和协调轮数有上限。准备子任务继承父任务范围并独立验证，父任务原需求和验收不变。

一轮内工作者并行编写并交换消息；全部退出后才开始该轮独立验收和集中集成，避免慢检查阻塞仍在运行的工作者通信。通过检查由控制器提交。候选合并先在单独 worktree 完成，再重验所有已集成任务；全部任务到齐时增加最终集成检查。通过后以期望旧提交值更新本地 integration 引用，避免覆盖外部变更。

独立审阅使用本次审阅工作区中新执行的固定检查证据，模型负责源码判断与补充反例；修复轮优先看同基线增量，最终整合仍看完整分支。适用条件、证据保护与耗时记录见 [code-review.md](code-review.md)。

合并冲突或组合测试失败保留候选现场，给负责工作者一份失败反馈；新尝试从最新已通过的集成提交开始，读取之前工作区的证据重新实现。不会自动选择 ours/theirs，也不会强制丢弃失败代码。最终另建审计工作区复验，delivery.json 记录真实结果。

## 恢复与边界

`cancel` 写停止请求，进程看护停止本次运行启动的子进程并保留工作区；`clear-cancel` 明确解除停止。正常停止后重新 run 继续未完成任务。时间、尝试次数与协调轮数跨重启保留。

异常中断用 `recover`：先确认记录的控制器与工作者都退出，再核对启动意图、提交意图和集成引用。发生在“已创建工作区但尚未启动”“已提交但未保存状态”“已更新集成引用但未确认”的崩溃分别处理；未确认的部分工作保留并从新分支继续。外部 Git 历史变化拒绝自动接纳。硬崩溃期间时间保守计入总预算。恢复后仍需 clear-cancel（若存在请求）和 run。

协调数据库与文件邮箱是合作式协议，不是操作系统权限隔离。正式服务器仍需要独立账户或容器、只读验收资源、供应商认证、预算和服务守护。Git worktree 不隔离网络、数据库与系统进程。本版没有部署服务器，也不保证每种 harness 都会及时读取消息；看护和独立验收用于发现违约，不能把消息文本当成执行证据。

失败证据进入包内错题本；每次派工按项目、harness 和环境筛选少量相关记录放入任务卡。跨运行复用时在 worker 配置指定稳定的 `memory_dir` 与 `memory_project`。未解决记录只是诊断线索，不会因为后一次成功就自动成为通用经验。

## 验证

`python -B -m unittest -v test_workflow_coordinator`（工作目录 scripts）覆盖模拟子进程的并行通信、里程碑、总控回复、越界、冲突、组合失败、消息去重、原子状态、版本变化、人工验收和恢复。真实模型案例另外记录；不能把这些模拟测试统计成模型成功率。
