---
name: dev-workflow
description: 实现或续接开发任务：用内置 grill-me 按项目复杂度对齐需求，拆成可验收任务并持续实施；支持当前会话、夜间准备、多分支协作和进度续接。不用于纯咨询、单独审查或编辑技能本身。
metadata:
  version: "2.18.0"
  compatibility: Codex and Claude Code with file and command tools; Python 3.10+ for the optional controller.
---

# dev-workflow

以本文件所在目录为 `WORKFLOW_ROOT`。默认在当前会话完成工作，按需读一个阶段指南。八个原有模块随包保留，日常开发不必逐个加载或安装。首次安装见 [README](README.md)。

输出默认英文；优先使用用户明确选择、当前计划语言和本地偏好，规则见 [language](references/language.md)。指南语言不决定产品文案或输出语言。

## 默认这样推进

1. **对齐目标**：先读现状和已有讨论；存在会改变交付的未知决定时，再用内置 [grill-me](references/modules/grill-me/guide.md)。已有答案直接复用；明确的小改动用一句话对齐即可，不重新跑访谈。较大功能先核对完整用户路径和范围，不能自行缩成 MVP。需要结构化确认时读 [需求对齐](references/requirements-alignment.md)。
2. **先计划再实施**：先按本次职责拆模块，再写步骤、依赖和验收。小任务用同一份简短记录，较大任务按 [planning](references/planning.md) 准备完整计划。复用项目已有文档，不为同一答案建立多份档案。
3. **连续执行**：只加载当前需要的指南，使用项目现有工具实现和验证；根据真实结果调整下一步。默认一个会话，不自动增加代理、独立模型进程或新依赖。已有流程图或用户要求可视进度时，按 [session-view](references/session-view.md) 维护实际阶段与计划变化；其他任务沿用现有计划记录。用户已有显示偏好优先。
4. **按证据交付**：完成原范围内的功能及必要回归，报告结果和未验证项。失败先看证据，避免原样重试。任务未完成就继续，直到达成目标、预算用尽、宿主停止或确需用户决定。推送、合并、部署沿用真实授权。

## 当前只读对应入口

| 当前工作 | 指南 |
|---|---|
| 所有前端实现、视觉设计与调整 | [frontend](references/frontend.md)：工程骨架与质量边界，审美由 AI 按项目决定 |
| API、数据库、后台任务 | [backend-development](references/modules/backend-development/guide.md) |
| 项目级任务覆盖与细化 | [plan-depth](references/plan-depth.md) |
| 定位错误、检查代码、验证浏览器行为 | [debugging](references/debugging.md) / [code-review](references/code-review.md) / [browser-validation](references/browser-validation.md)，按当前问题选用 |
| 用户要求 PRD、独立 Issue 或测试优先 | [write-a-prd](references/modules/write-a-prd/guide.md) / [prd-to-issues](references/modules/prd-to-issues/guide.md) / [tdd](references/modules/tdd/guide.md) |

Taste、Impeccable 和综合前端审查是可选工具。用户点名，或一个具体问题需要其工具时，按 [skill-routing](references/skill-routing.md) 选取；普通前端任务从 frontend 开始，不自动叠加整套设计模块。实现阶段有明确上下文时，由 AI 自主选择配色、字体、布局、素材和动效。

## 只有需要时进入独立自动运行

用户要求夜间、服务器、多分支、逐功能 PR 或额度主备续跑时，读 [autonomous](references/autonomous.md)，再准备其要求的固定计划、验收、预算和运行环境。现有控制器与独立审阅规则保持适用；普通会话无需先生成这些配置。

收到控制器 task card 时，继承已确认需求和计划，先完成卡片指定的消息预检，再实现本任务；不重新访谈或规划整个项目，不自行启动代理、推送或合并。实现就绪后回报 ready_for_verification 并退出，由总控验收；本地缺验证工具不等于实现阻塞。需要协议细节才读 [worker-protocol](references/worker-protocol.md)。

同目标续接用已有记录和检查点，只补新决定。进度、日志、经验放项目或任务目录，不能写入 Skill 安装目录；详见 [checkpoints](references/checkpoints.md)、[learning-loop](references/learning-loop.md) 和 [acceptance](references/acceptance.md)。旧运行继续其冻结版本，不改旧 pins。用户当前指令与宿主规则优先。
