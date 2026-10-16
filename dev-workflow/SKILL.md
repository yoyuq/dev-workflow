---
name: dev-workflow
description: 实现或续接开发任务：用内置 grill-me 按项目复杂度对齐需求，拆成可验收任务并持续实施；支持当前会话、夜间准备、多分支协作和进度续接。不用于纯咨询、单独审查或编辑技能本身。
metadata:
  version: "2.17.1"
  compatibility: Codex and Claude Code with file and command tools; Python 3.10+ for the optional controller.
---

# 统一开发工作流

以本文件所在目录为 `WORKFLOW_ROOT`。内置模块随包提供，不要求用户逐个安装。第一次安装看 [快速开始](README.md)；用户只需描述目标，配置文件和执行命令由 AI 准备。

**Output language**: Default to English. Before interviewing or writing a new plan, honor the user's explicit language choice, then an existing card/plan locale, then the local preference (`python WORKFLOW_ROOT/scripts/workflow_locale.py`). Use the resolved language for questions, titles, progress and reports. The guides' language is not the output preference. Record `locale` in new plans; see [language.md](references/language.md). Original logs, commands and evidence remain verbatim.

**收到控制器准备好的 task card**：复用卡片的需求确认和计划，先完成卡片要求的文件消息预检，再读本任务指南并实施；不重新访谈、规划整个项目或启动代理。Python 消息助手可选。实现就绪后发 ready_for_verification 并及时退出，由总控验收；本地缺验证工具不等于实现阻塞。协议细节仅在需要时读 [worker-protocol.md](references/worker-protocol.md)。

## 按目标规模推进

1. 新目标先读 [内置 grill-me](references/modules/grill-me/guide.md) 和 [需求对齐](references/requirements-alignment.md)。先判断是局部修复、完整功能还是项目改造；复用真实答案、调查代码事实，逐个追问会改变交付的缺失决定。项目改造要覆盖完整用户路径、现状差距与适用的界面/数据/运行边界，不能把“少问”当成访谈目标。同目标续接不重复询问，记录实际用户确认。
2. 按 [planning.md](references/planning.md) 先拆模块，再拆可验收任务并写计划。项目级任务使用 [深度与覆盖检查](references/plan-depth.md)，由同一份计划生成文档与流程图；不能把前端、后端、测试三个大节点当成完整拆解。小修复仍可用短计划。配置、路径和 JSON 由 AI 处理，不交给用户手填。
3. 默认在当前会话中实施，只加载下面对应的指南。按 [会话流程图](references/session-view.md) 登记实际节点并打开只读预览；已有记录/面板则续用。开始节点、动作或计划变化、验证结束时同步进度。使用项目现有工具验证，再根据结果继续。普通修复不启动独立控制器或多个模型进程；本机预览只读取进度。用户不要预览或环境不支持时明确回退到聊天与计划记录。
4. 按 [checkpoints.md](references/checkpoints.md) 保存必要进度；失败先查证据再换方案，停止没有新信息的原样重试。对未完成工作持续推进，直到目标达成、预算用尽、宿主停止或确实需要用户决定。
5. 按 [acceptance.md](references/acceptance.md) 对照每条已确认需求交付检查结果和剩余问题。只做通一条路径是中间里程碑；其余已承诺功能仍要推进，不能静默缩成 MVP。范围完成度看需求与证据，不看改动行数。自动运行默认交付分支提交与报告；推送、合并、部署按实际用户授权处理。

## 当前阶段只读对应模块

| 任务 | 入口 |
|---|---|
| 需求对齐或关键需求变化 | [grill-me](references/modules/grill-me/guide.md) |
| 正式产品需求文档 / PRD 拆任务 | [write-a-prd](references/modules/write-a-prd/guide.md) / [prd-to-issues](references/modules/prd-to-issues/guide.md) |
| 官网、落地页、作品集 | [design-taste-frontend](references/modules/design-taste-frontend/guide.md) |
| 业务界面、应用 UI、界面完善 | [impeccable](references/modules/impeccable/guide.md) |
| API、数据库、后台任务 | [backend-development](references/modules/backend-development/guide.md) |
| 用户要求测试优先 | [tdd](references/modules/tdd/guide.md) |
| 故障定位 / 代码检查 / 浏览器流程 | [debugging](references/debugging.md) / [code-review](references/code-review.md) / [browser-validation](references/browser-validation.md) |
| 较大界面的综合验收 | [frontend-interface-review](references/modules/frontend-interface-review/guide.md) |

后端按项目选技术栈，已有项目沿用现状；必要的模块工具适配见 [skill-routing.md](references/skill-routing.md) 和 [adapters](adapters/README.md)。不要为小任务加载整包指南。

## 需要独立自动运行时

用户要求夜间运行、独立进程、可恢复的多分支协作时，读 [entrypoint.md](references/entrypoint.md)。先用 `workflow.py setup` 生成配置，再用 `doctor` 检查；`doctor --live` 才实际调用模型并消耗额度。AI 准备计划和固定验收，确认真实环境可用后才派工。离线演示 `self-test` 是模拟工作者，不能代替真实模型或项目验收。

单任务用 quick；多模块协作、受控重规划或需要完整独立审阅用 full。full 必须配置 reviewer；[coordination.md](references/coordination.md) 管理分支、消息与依赖。需要改实施路径才读 [replanning.md](references/replanning.md)，需要真实用户答复才读 [human-resume.md](references/human-resume.md)。不改目标、原验收、预算或旧运行的 pins；旧运行继续使用冻结版本。

用户要求额度耗尽后自动推进时，按 [额度恢复](references/provider-recovery.md) 预先固定主备入口并启用quota_recovery，再把运行交给Python控制服务。主入口受限自动换备用；全部受限保存进度、退避等待并续跑同一实施尝试，不依赖人工点击。认证/代码错误不混为额度，原时限、返工次数和验收不刷新；旧运行不改pins就地升级。

用户要求逐功能远程 PR 自动推进时，读 [功能子 PR](references/feature-prs.md)。先调查改进方向与远程仓库，再按用户可验收的功能拆任务。每次运行有一个专属工作分支；子 PR 经固定检查与独立审阅合并到它，实际合并后回归通过才推进下一功能。已授权的回归失败自动普通 revert、验证恢复、交回原任务修复重试；项目基线分支保留待审草稿 PR。远程操作由总控负责，工作者不自行 push/merge。

新功能分支按 [任务拆分](references/task-partitioning.md) 评估复杂度、依赖、风险和可独立恢复的边界，记录理由并检查规模提示。需要运行中自动细分时固定 partitioning、allow_feature_split 及 AI 协调者配置；子任务只选原检查、继承范围与依赖，原任务保留完整验收和原预算。原子改动可有证据地保留，不能按节点数或行数机械拆分。

失败证据与候选经验按 [learning-loop.md](references/learning-loop.md) 保存；未经匹配验收和独立支持不能提升为已验证经验。跨会话复用见 [memory.md](references/memory.md)，状态保存在项目/任务目录，不写 Skill 安装目录。服务部署仅在需要时读 [service.md](references/service.md)；安装 Skill 不会自动启动服务。

查看进度和故障时使用 [运行看板](references/observability.md)，以流程图显示任务依赖与状态，点击节点再查看尝试、时间线和日志；日志现象与排查建议不能代替根因验证。

真实任务的耗时、模型用量和人工介入按 [metrics.md](references/metrics.md) 记录，使用 `workflow.py metrics` 汇总 SDK usage 与缺失覆盖率。未知金额、被中断调用和宿主救援不能按零处理；新交付测试必须实际执行，启动修复必须核验真实共享配置。

准备无人值守运行时，按 [自动检查与有限恢复](references/automatic-assurance.md) 固定补充风险检查及其任务负责人；总控检查失败后交回原工作者修复。需要有限审阅进程恢复时，在原计划声明 `review.max_recovery_attempts`，不扩预算或放宽批准条件。

包版本 2.15.0 与协调计划 v2、旧串行计划 v4 是不同概念。[迁移和安装](references/portability.md) · [第三方来源与许可证](THIRD_PARTY.md)。用户当前指令和宿主规则优先；本包不会自行授予外部操作或权限变更。
