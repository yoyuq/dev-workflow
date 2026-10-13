# 一个目录的安装与迁移

第一次使用从 [README 快速开始](../README.md) 进入。普通开发只需安装和描述目标；环境检查、配置生成和离线演示统一通过 workflow.py 的 doctor/setup/self-test。

把完整 `dev-workflow/` 目录交给另一个 harness。根 SKILL.md 是唯一被发现的 Skill；内置 8 个模块入口用 guide.md 命名，资源按需读取。无需另外安装它们。基础包无 pip 依赖；运行项目仍需要项目自身环境。

```text
python <解压目录>/dev-workflow/scripts/workflow_portable.py doctor
python <解压目录>/dev-workflow/scripts/workflow_portable.py install --harness codex --user
python <解压目录>/dev-workflow/scripts/workflow_portable.py install --harness claude --user
```

Codex 新用户安装目标为 `~/.agents/skills/dev-workflow`，Claude Code 为 `~/.claude/skills/dev-workflow`。项目安装把 `--user` 换成 `--project <项目目录>`；或直接用 `install --dest <明确目标目录>`。命令中的占位符需换成真实路径并按 shell 引用。

已在 `~/.codex/skills/dev-workflow` 使用本技能的 Codex 用户，应升级原位置并避免创建同名第二份安装。安装器会拒绝检测到的用户级旧/新双路径冲突；它不会替你删除任何旧技能。内容相同可重复安装，内容不同会拒绝覆盖。升级前备份现有目录，再由操作者明确替换；保留包外状态目录。

## 三类可迁移内容

| 内容 | 位置 | 迁移方式 |
| --- | --- | --- |
| 工作方法、模块与脚本 | Skill 目录 | 整目录复制，doctor 校验 |
| 项目目标、计划、经验、证据 | 项目/任务目录 | 随项目复制，恢复前核对代码 |
| 模型登录、权限、Git、浏览器、数据库 | 目标运行环境 | 单独配置并实际验证 |

package-manifest.json 检查包内所有发布文件的完整性，不提供来源签名。module-index.json 另记录原模块快照摘要。不要在安装目录写入任务状态、缓存或临时运行副本；第三方启动器需要原始布局时使用 prepare-module。

## 验证范围

包内自动测试覆盖文件完整性、单入口、安装幂等/拒绝覆盖、模块副本、记忆持久化和夜间检测器。它们不证明两种模型都会遵守每条指令。迁移到新 harness 后，用一个小任务验收：先计划、执行、实际测试、写检查点、重新进入会话、读取记录并接续。

新任务由 [统一入口](entrypoint.md) 和 [事件驱动服务](service.md) 提供持续推进、持久队列、状态页和部署配置；本包尚未在真实独立服务器完成上线。旧 workflow_runner.py 的使用方法见 [runner.md](runner.md)。迁移已有运行时，绝对路径和 CLI 路径必须重新准备并固定新快照，不直接修改运行中的 pins.json。

规范依据：[Agent Skills specification](https://agentskills.io/specification)、[Codex skills](https://learn.chatgpt.com/docs/build-skills)、[Claude Code skills](https://code.claude.com/docs/en/skills)。

包内 [总控与多分支协作](coordination.md) 携带 SQLite 消息与事实管理、文件邮箱、分支执行和 AI 协调适配，不需要另装协作 Skill。2.5 新任务统一采用 coordinated v2，支持固定策略内重规划、独立审阅及错题本闭环；保留原串行 v4 入口。旧运行保留原冻结技能，不覆盖安装目录后强行续跑。迁移运行状态还需携带受管理 worktree 所属 Git 仓库，不能只复制 SQLite 就宣称恢复完成。
