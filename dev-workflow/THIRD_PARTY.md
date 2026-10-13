# 内置来源与修改说明

本包汇集用户已有技能快照，时间为 2026-10-09。来源与逐文件摘要见 module-index.json。未确定这些本地文件各自对应的精确上游 commit，因此不声称它们是上游最新版本或某一完整发行版。

| 内置模块 | 来源与许可 |
| --- | --- |
| design-taste-frontend | [Leonxlnx/taste-skill](https://github.com/Leonxlnx/taste-skill)，MIT，见 licenses/taste-MIT.txt |
| impeccable | [pbakaus/impeccable](https://github.com/pbakaus/impeccable)，Apache-2.0，见 licenses/impeccable-Apache-2.0.txt |
| grill-me、tdd、write-a-prd、prd-to-issues | [mattpocock/skills](https://github.com/mattpocock/skills)，MIT，见 licenses/mattpocock-MIT.txt |
| frontend-interface-review、backend-development | 当前用户本地定制版本，保留原内容；不将其归为上述仓库原作 |

这些模块的文件内容按本地快照原样保留，根 SKILL.md 重命名为 guide.md。路径/调用适配在统一入口及 adapters/、references/skill-routing.md 中说明；没有为适配直接重写第三方指南或脚本。prepare-module 只在任务副本恢复原文件名。

许可证文本来自上述仓库公开 LICENSE。保留原有版权声明及适用许可；本说明不为第三方内容重新授权。Impeccable 的运行引擎、浏览器及项目依赖不随本包提供；它们适用各自许可和安装要求。

debugging.md、code-review.md、browser-validation.md、agent-loop.md 与记忆/适配脚本是本次新增的统一工作流资源，不是额外安装的第三方同名 Skill。公开方法参考链接在相应文档中。
