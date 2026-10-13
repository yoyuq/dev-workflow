# Claude Code

完整目录安装到用户 `~/.claude/skills/dev-workflow` 或项目 `.claude/skills/dev-workflow`。通过 `/dev-workflow` 或自然语言调用。读取内部 guide.md 即可，不需要在 Claude Code 逐一安装同名模块。

用当前工具完成文件读写、命令执行和必要澄清。不依赖 Codex 的 agents/openai.yaml、桌面能力或工具名称。若宿主提供 Skill 路径变量可使用，否则从已加载 SKILL.md 的实际路径解析。

本包不修改 CLAUDE.md、settings、hooks、MCP 或权限配置。用户级本地安装也不代表云端环境已安装。实际模型遵循情况、浏览器集成和账户认证需在目标环境用小任务验收；本包测试只验证资源布局和 Python 行为。

参考：[Claude Code skills](https://code.claude.com/docs/en/skills)。
