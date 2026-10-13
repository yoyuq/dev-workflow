# 串行控制器

`scripts/workflow_runner.py` 是可运行的 Python 标准库控制器。总目标 → 已准备的模块和计划 → 一轮任务卡 → harness 实施 → 独立检查 → 失败反馈 / 分支提交 → 下一个就绪任务。无需给每一步安排定时任务。

## 准备与启动

1. 按 planning.md 与 acceptance.md 准备 v4 计划，生成执行计划书，选定隔离的 Git 仓库和干净任务分支；配置该仓库提交身份。核实已有用户授权，保留真实授权引用。
2. 把计划、独立验收脚本、运行配置与状态目录放在 agent 仓库之外。计划固定整个 Skill 的目录摘要。独立验收脚本的绝对路径会被固定摘要；仓库内的测试仍可作为开发反馈，不能冒充隔离的验收根。
3. 复制 assets/runtime.example.json 并填写实际 CLI 路径。kind 选 codex 或 claude；model 省略则沿用当前配置。Claude 可选 max_budget_usd；这只是 CLI 单次限制，不能代替总账户费用控制。
4. 配置必要 preflight_checks：每项为 id、argv 字符串数组、timeout_seconds。用于数据库、语言运行时或浏览器的真实健康检查；输出只留摘要，失败即阻塞。--version 成功不证明模型认证正常。
5. 顺序执行：

```text
python <WORKFLOW_ROOT>/scripts/workflow_runner.py init --plan <prepared-plan.json> --runtime <runtime.json> --run-dir <new-run-dir>
python <WORKFLOW_ROOT>/scripts/workflow_runner.py preflight --run-dir <run-dir>
python <WORKFLOW_ROOT>/scripts/workflow_runner.py run --run-dir <run-dir>
python <WORKFLOW_ROOT>/scripts/workflow_runner.py status --run-dir <run-dir>
```

路径示例是占位符，按宿主 shell 引号规则替换。init 只接受新的状态目录；不覆盖已有运行。Codex 使用 exec、JSON 事件、workspace-write、never approval、ephemeral；Claude 使用 print/stream-json、dontAsk、工具允许列表，不使用跳过权限开关。Windows 需要原生 exe；.cmd/.bat/.ps1 shim 不会被当作可执行文件直接调用。CLI 参数兼容性和登录必须在目标主机实际试跑。

## 每轮发生什么

- 检查计划、技能、独立验收输入和 CLI 是否漂移；从依赖满足的任务中取一个。
- 生成 task-card-N.json 和 prompt-N.txt，附少量适用经验与上一轮失败检查的日志末尾。模型可在范围内改变实现方法；目标、验收、范围不可改。
- 保存 attempt 次数后启动 CLI，记录进程 ID、事件、stderr、首个文件变化时间与退出原因。token/thinking 事件只算存活；文件变化也只算候选进展，不算通过验收。
- 按 attempt_seconds、no_progress_seconds、max_output_bytes 看护进程。长时间诊断可能没有文件变化，准备阶段应按任务调整无产物时限。检查失败时把真实日志交回下一轮；尝试次数不因重启重置。
- CLI 明确报告账户额度不足或认证失败时标记 blocked_environment，停止队列，不把它当代码缺陷反复重试。其他未知错误保留真实日志并遵循有限重试；文字分类无法覆盖每种供应商返回格式。
- 重新运行本任务和所有已交付前置任务的检查；通过后只暂存允许范围内的改动，创建本地分支提交。存在 manual 条件时保留 awaiting_review，其依赖继续等待。
- 结束时由原有 v4 验收器重验最终代码，生成 delivery.json、独立 audit.json 和 layered-acceptance.json。最终复验受剩余总时间限制；超时不能标为完成。

退出码：0 为本轮全部完成；3 为部分交付、时间耗尽或待人工验收；2 为环境、状态或输入阻塞；130 为中断。具体原因看 state.json 和 controller.events.jsonl。

## 恢复与取消

同一 run-dir 再执行 run 会跳过已验收任务。`run --max-tasks 1` 可在一个已验收任务后停下，用于检查分阶段恢复。运行状态是控制器的事实来源，模型无需再写一份冲突 checkpoint。

`cancel --run-dir ...` 写停止请求；执行器停止自己启动的进程树并保留现场。恢复前用 `clear-cancel --run-dir ...` 明确移除请求，随后 run。已花费时间和尝试次数保留；预算到期则准备新的运行快照。

硬崩溃遗留 runner.lock 时，先 inspect 状态。`recover-lock` 仅在记录的控制器和工作进程均已退出时删除该锁，PID 复用会保守拒绝。部分代码与最后快照不一致时不会自动采纳或删除：检查 diff 后运行 `reconcile --run-dir ... --task T1 --expected-head <本任务基线提交> --reason <检查结论>`。它只接纳本任务允许范围内的未提交变化，重新验收仍必需；分支/历史变化或越界文件拒绝自动恢复。崩溃期间时间保守计入预算。

失败后若工作区存在部分代码，停止当前串行队列，避免污染独立任务。没有工作区变化时可继续其他不依赖失败项的任务。不会强制 reset、丢弃工作或把失败代码提交为完成。

## 记忆与可信边界

可在运行配置设置绝对路径 memory_dir 与稳定的 memory_project，让同项目的多次运行共享经验；默认写在 run-dir/memory。范围、有效期、使用反馈见 memory.md。控制器自动记录失败为 unresolved；不会根据“下一次成功”就自动推导通用因果结论。可复用修复经核实后用 lesson/feedback 命令归档。

本工具是进程控制器，不是操作系统安全边界。摘要能检测漂移，不能阻止同一用户权限下的恶意进程同时修改代码和控制文件。正式服务器应把可信控制目录、验收脚本、凭据和仓库按进程/账户/容器隔离；本包未替用户部署这些资源。模型提示、路径范围检测也不能替代沙箱。

总运行时间、单次时限、输出量和尝试数被控制器执行；停止进程、Git/磁盘 I/O 有有限清理耗时，因此总墙钟可能略超配置时长。非空 plan.limits.cost_limit 会被拒绝，因为当前控制器不能跨供应商严格核算金额。需要费用硬上限时配置供应商账户配额，不把日志估算当作账单保证。

command 适配器用于可信测试驱动或明确编写的集成；其结果必须标明“模拟”，不能统计为真实 AI 完成率。模型认证、Linux 行为和服务器守护需要在各目标环境另外验证。
