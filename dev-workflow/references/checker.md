# 夜间 Python 检测脚本

入口：[scripts/check_night_run.py](../scripts/check_night_run.py)。Python 3.10+ 和 Git 即可，不依赖 pip 包。支持 Windows 与 Linux 的路径/子进程处理；本包在当前 Windows 主机进行了临时仓库实测，部署到 Linux 后仍需运行附带测试和项目试跑。

## 调用位置

grill-me 对齐需求并记录用户确认 → AI 完成模块拆解 → 编写任务与验收安排 → 从同一份准备数据生成正式计划书和任务 JSON → 外部控制器固定输入 → **preflight** → AI 按计划实现、验证和提交 → 模型报告 → **verify** → 保存机器验收结果和分支，次日审阅。

`preflight` 只检查条件，不启动 AI，也不运行测试命令。`verify` 会真实执行计划约定的验证命令；先检查仓库、报告和范围，条件不成立时直接拒绝。preflight 检查 ready 任务的环境，verify 检查被报告为完成任务的环境；blocked/失败任务的运行依赖不会影响独立任务的部分交付。两阶段均不创建提交、推送、合并或部署。

检查器、计划快照和运行证据目录由外部控制器管理，放在 AI 工作区之外。摘要由控制器在 AI 启动前固定，不能在交付时根据模型改过的计划重新计算。传入摘要本身不代表授权，`trusted_authorization_ref` 的真实性仍由控制器核对。

## v4 计划字段

先按 [planning.md](planning.md) 完成模块方案和任务步骤，填入 [任务模板](../assets/night-run.template.json)，使用 render-plan 生成正式计划书。模板保持 draft，空字段会被拒绝。v1–v3 计划需补齐需求/验收关联与模块依赖绑定，重新生成计划书并固定摘要；不要仅修改版本号。详细例子见 [验收覆盖与计划一致性](acceptance.md)。

| 字段 | 约定 |
| --- | --- |
| `run_id` | 本轮唯一标识；报告必须相同 |
| `requirements_alignment` | 2.3 起必填；完整字段见 [需求对齐](requirements-alignment.md)。执行要求 confirmed、真实用户确认引用和原话、无未决问题；草稿可先 render-plan，但不能 preflight 或开跑。脚本不鉴定原话真实性 |
| `requirements` | 非空需求清单，每项有唯一 id 和 description；每条需求至少关联一个任务验收条件 |
| `execution_plan` | `{"path": "执行计划书快照的绝对路径", "sha256": "文件 SHA-256"}`；快照须在 AI 工作区外，为非空 UTF-8 文件 |
| `modules` | 非空模块清单；每项有唯一 `id`、`name`、`responsibility`、`inputs`、非空 `outputs` 和 `depends_on`；依赖存在且无循环 |
| `tasks[].module_ids` | 非空，引用本轮模块 ID；每个模块至少关联一项任务，跨模块任务可引用多个模块 |
| `tasks[].module_dependencies` | 将外部依赖模块映射到前置产物提供任务，例如 `{"M-api": ["T-contract"]}`；任务须属于提供方模块，且是本任务的直接或间接前置任务。无外部模块依赖时填 `{}` |
| `project.repository` | 本次隔离 Git 工作区的绝对本地路径，必须是仓库根目录 |
| `base_ref` / `base_commit` | 来源分支及完整提交 ID；实际基线以提交 ID 为准 |
| `work_branch` | 控制器已创建并切换的独立任务分支；preflight 要求 HEAD 等于基线 |
| `frontend_surface` / `backend_stack` | 准备阶段选定值；不涉及时填 `none` |
| `decision_record` / `trusted_authorization_ref` | 已确定的决策记录与外部授权来源引用 |
| `skill_versions` | 技能名映射到 `{"path": "技能目录绝对路径", "sha256": "完整目录摘要"}` |
| `tasks[].status` | 初始值 `ready` 或 `blocked`；blocked 必须有 blockers，ready 不得有 |
| `depends_on` | 同计划的任务 ID；禁止循环依赖、未知依赖或 ready 任务依赖 blocked 任务 |
| `allowed_scope` | 仓库相对的文件或目录前缀，使用 `/`；例如 `src/api`、`tests/api`、`package.json`。不是 glob，不能用 `.` 表示全仓库 |
| `required_skills` | 非空，必须逐个出现在 skill_versions 中 |
| `acceptance_criteria` | 非空对象数组：任务内唯一 id、description、非空 requirement_ids、mode、verification_ids；自动条件至少关联一个检查，人工条件须有 review_instructions 且检查关联为空 |
| `verification` | 检查清单，每项有唯一 id、参数数组 argv、相对 cwd、正整数 timeout_seconds；全部检查都需关联自动条件。仅含人工条件的任务允许空数组 |
| `limits` | ready 任务数不得超过 max_tasks；脚本约束每个检查及本次验收总时长。AI 全程时长、重试和费用仍由控制器落实 |

技能目录摘要包含 SKILL.md 及 references/scripts/assets 等文件，按相对路径排序，忽略 `.git`、`__pycache__`、`.pyc`、`.pyo`。不支持技能目录内符号链接。不要使用依赖清单中的单个 SKILL.md 摘要来替代目录摘要。

```sh
python check_night_run.py hash-skill /opt/skills/dev-workflow
```

验证命令示例：

```json
{"id": "api-integration", "argv": ["python", "-m", "pytest", "tests/integration"], "cwd": ".", "timeout_seconds": 300}
```

命令采用参数数组和 `shell=False`，不会解释 `&&`、管道或重定向；需要多个步骤时写成多个 verification 条目。Windows 下 `.cmd`/`.bat` 不直接执行，例如 npm 使用 `node` 和实际的 `npm-cli.js` 路径。项目命令和运行依赖由准备阶段确定，不自动猜测或安装。验证命令必须适合重复执行、在隔离测试资源上运行，不得包含推送或部署。

## 准备与两阶段检测

填写好 v4 准备 JSON 后，先生成正式计划书；execution_plan 此时可为 `{}`，status 可以是 draft：

```sh
python check_night_run.py render-plan --plan /srv/runs/run-001/plan.json --output /srv/runs/run-001/execution-plan.md
```

命令输出 `{"path": "...", "sha256": "..."}`。将其填回 execution_plan；确认内容就绪后设置 ready，再让控制器固定 JSON 的摘要。render-plan 不修改输入 JSON、不接受覆盖已有文件，也不会授予开工权限。准备阶段改变内容后须使用新输出文件重新生成。

下例从技能 `scripts` 目录调用。计划和报告路径替换成服务器上的真实路径，`PINNED_SHA256` 替换成控制器保存的计划文件 SHA-256。Windows 可以使用本机绝对路径。

```sh
python check_night_run.py preflight --plan /srv/runs/run-001/plan.json --plan-sha256 PINNED_SHA256 --output-dir /srv/runs/run-001/preflight
python check_night_run.py verify --plan /srv/runs/run-001/plan.json --plan-sha256 PINNED_SHA256 --report /srv/runs/run-001/result.json --output-dir /srv/runs/run-001/verification-01
```

每次使用新的 output-dir，脚本拒绝覆盖已有运行记录。正常进入检查后生成 audit.json，verify 另存真实 stdout/stderr 日志、日志摘要、退出码、耗时和对应 HEAD。acceptance 清单将这些结果关联到条件和需求；未运行不会写成通过。计划不能读取或输出目录不能建立时，失败记录仍输出到 stdout，控制器应始终保存 stdout/stderr。

两个入口都会核对计划书摘要，并根据准备数据重新渲染全文进行比较；verify 在命令执行后再次核对。audit.json 保存文档路径和摘要。脚本能检查准备数据与文档是否一致，不能判断业务方案是否合理或证明先写计划再写代码；内容审查和准备顺序仍由工作流与控制器落实。

模型报告遵守 [结果 schema](../assets/night-result.schema.json)。commits 按先后顺序列出本地 base_commit..HEAD 全部提交；checks 的 check_id 对应计划检查。action、evidence 只作说明，不作为代码或通过证明。脚本重跑已报告完成任务及 awaiting_review 任务所需的全部自动检查。

含 manual 条件的任务不能出现在 completed_task_ids；实现和自动检查完成后，写入 pending_tasks，状态为 awaiting_review，并给出 reason 与 next_action。仅此类待验收交付也可使用整体 partial，退出码为 3。模型不能用 awaiting_review 掩盖自动检查失败，也不能自行提交“人工通过”证据。当前人工最终确认在外部交付记录中保存。

| 退出码 | 含义及控制器处理 |
| --- | --- |
| `0` | preflight 条件通过，或 verify 全部任务通过；必须同时查看 phase/completed，不能把开工检查当作交付完成 |
| `1` | 拒绝、验证失败或环境错误；查看 errors 和检查日志 |
| `2` | 命令行参数错误 |
| `3` | 报告及交付部分通过检查，但还有 pending/blocked/failed/timeboxed/awaiting_review 任务；本轮不算全部完成 |
| `130` | 收到中断；保留现场 |

计划检查拒绝无验收关联的需求、无检查关联的自动条件、无效模块/任务绑定、计划书内容不一致等问题。交付检查还拒绝空完成报告、漏报/重复任务、未完成依赖、伪造提交、缺少或失败的检查、脏工作区、错误分支、技能变更和范围外的已提交文件。范围按已完成及 awaiting_review 任务允许路径的并集检查，包含中途修改后又恢复的文件；这不能证明每个提交属于哪项任务。仅支持线性提交，不接受合并提交。

## 能力边界与部署验证

- 自动检查只验证可执行条件与约定命令的结果，不能判断测试是否充分，也不能代替前端视觉审查和业务验收。模型能改写的测试可能被弱化；关键独立验收测试应由控制器另存并固定版本。
- 这不是沙箱或恶意代码防护。隔离执行、权限、数据库资源、网络、日志磁盘限额、任务锁和调度由外部执行器负责；Git 干净状态不覆盖 ignored 文件或外部系统。
- 检查超时会尝试终止子进程树；Windows 使用 taskkill，Linux 使用进程组。脱离进程组的进程以及主程序退出后的后台进程仍应由执行器的容器/cgroup/Job Object 整体回收。
- 计划摘要检查依赖外部控制器保护预期摘要及脚本，不能抵挡拥有相同系统权限的进程篡改控制器。检查通过不表示 AI 一定实际读过每个技能。

部署后先运行自测，再选择一个小项目任务进行完整试跑：

```sh
python -B -m unittest discover -s . -p "test_*.py" -v
```

自测仅创建临时 Git 仓库，不调用模型、不访问生产服务。可通过 `WORKFLOW_TEST_TMP` 指定临时目录。测试通过仍不等于服务器调度和实际项目依赖已配置好。
