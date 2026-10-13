# Codex

通过 `$dev-workflow` 或自然语言启用；后续阶段直接读包内指南。根目录 agents/openai.yaml 是 Codex 可选元数据，其他平台不需要解释它。

新安装可用用户目录 `~/.agents/skills/dev-workflow` 或项目 `.agents/skills/dev-workflow`。已有安装在 `~/.codex/skills/dev-workflow` 的环境应升级原位置，避免重复安装同名入口。以当前 Codex 版本实际发现路径为准。

把通用读写/命令动作映射为当前环境工具。桌面浏览器、连接器及子代理可用性不能推定服务器 CLI 同样具备。除非用户明确要求，不创建应用 Goal、新聊天或定时自动化。连续推进由当前活跃 agent 执行；独立进程控制器属于另一个交付物。

参考：[Codex skills](https://learn.chatgpt.com/docs/build-skills)。
