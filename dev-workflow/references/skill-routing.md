# 内置模块路由与衔接

所有基础阶段资源已收进本包，入口是 `modules/<模块名>/guide.md`。这里的模块指技能资源，计划中的模块指软件功能模块，两者分别记录。

## 统一调用约定

直接读取指定 guide.md，按需读取其同目录支持资源。不调用另一个已安装的同名 Skill，也不要求用户再安装 8 个技能。原文的 `/skill-name`、`$skill-name`、Skill 工具调用在这里表示加载对应包内指南；文件编辑、命令执行、浏览器操作映射到当前 harness 的可用能力。

传入上下文：用户目标、requirements_alignment 对齐记录、已确认答案、当前任务/功能模块 ID、允许改动范围、接口约定、代码状态、验收条件。grill-me 默认在新目标入口使用，具体见 [需求对齐](requirements-alignment.md)。后续模块的访谈由已有确认满足；只有确实缺少的新决定才询问。不能编造用户回答。无人值守时将依赖该决定的任务标记 blocked。

## 前后端与验收

- Taste 用于官网、落地页、作品集；多步骤业务应用交给 Impeccable。保留既有设计系统和项目约束。混合项目按页面职责选择，避免两套设计要求同时作用于一个页面。
- backend-development 使用原目录中的 references/verification.md；沿用项目技术栈，先明确接口及数据契约，再实施和验证真实联调。
- frontend-interface-review 原文要求定位 Impeccable 时，直接定位本包 `modules/impeccable`。明确审查结果和实际缺失的验证能力。
- 原 Impeccable 脚本需要名为 SKILL.md 的模块根文件时，使用 `workflow_portable.py prepare-module` 在任务运行目录生成副本，见 adapters/README.md。不要手工修改包内快照或把运行副本装进技能发现目录。
- Impeccable 原文引用自定义代理类型时，只有宿主支持且当前任务允许委派才使用。否则读取它的 `reference/degraded/` 对应角色说明，在当前 agent 中执行并明确是自审。包不会替用户注册代理、修改配置或安装浏览器。
- TDD 只用于已选定的测试优先任务；普通任务照样做与风险相称的真实验证。PRD 和独立 Issue 文件仅在需要时生成；“AFK”任务标签不是外部操作授权。

## 可选扩展

数据库、框架、语言专用 Skill 按实际栈追加。没有扩展时，阅读项目和对应版本的官方资料继续工作；不要将缺少某个推荐 Skill 判为整个工作流不可用。插件/MCP、浏览器、Git、语言运行时是环境能力，不能由文本模块替代。

固定夜间计划的 required_skills 通常只需 `dev-workflow`，skill_versions 固定完整统一目录的摘要。任务说明中记录所选内置模块。确实使用包外扩展时，才另行固定扩展版本和目录。旧计划的多技能摘要不能直接改名复用，应重新准备新快照。
