# 一个入口，两种流程档位

普通开发默认在当前会话执行；需求与节点细度按任务规模选择，项目改造用 [深度与覆盖检查](plan-depth.md)，不因为采用当前会话就减少范围。按 [会话流程图](session-view.md) 打开进度预览，无需启动控制器。新用户从 [快速开始](../README.md) 进入；AI 负责配置和计划，用户无需填写 JSON。

`workflow.py setup --project PROJECT --harness codex|claude` 自动发现原生 CLI，生成 runtime.json、project.json、START.md；默认输出项目旁 `.workflow-项目名`。多分支加 `--mode full`。可用 `--executable` 指定程序、`--model` 固定已验证模型、`--output` 指定新目录。它不记录虚构的需求确认，也不启动业务任务。

`workflow.py doctor --project PROJECT --runtime RUNTIME` 默认离线；也可用 `--harness` 直接检查。加 `--live --output NEW_DIR` 实际调用模型，限时要求文件读写回执。按实际配置检查 worker/reviewer/coordinator；相同配置共用同适配器探针。默认每种配置 90 秒，可用 `--seconds 5..300`；不包含浏览器/数据库/项目测试。`self-test --output NEW_DIR` 演示本地分支提交与实际断言，但工作者是模拟程序，不消耗模型额度。

新运行统一使用 `python scripts/workflow.py`。先完成 grill-me 对齐、模块拆解、执行计划和检查准备。新输入沿用协作 v2 格式，串行任务是只有一个任务的协调运行，不再维护另一套新任务协议。

```text
workflow.py init --plan PLAN --runtime RUNTIME --run-dir RUN --mode quick
workflow.py run --run-dir RUN
workflow.py status --run-dir RUN
workflow.py metrics --run-dir RUN
workflow.py report --run-dir RUN
workflow.py requests --run-dir RUN
workflow.py respond --run-dir RUN --input RESPONSE
workflow.py replan --run-dir RUN --input PROPOSAL
workflow.py recover --run-dir RUN
workflow.py retry-review --run-dir RUN --task TASK
workflow.py cancel --run-dir RUN
workflow.py clear-cancel --run-dir RUN
```

quick：一个任务、单工作分支、简短已确认需求和计划、外部检查、需求覆盖与报告；独立模型审阅按需要配置。full：多模块/有依赖/需要动态拆分/较高风险，必须提供 reviewer，可配置 coordinator 与受控重规划。未指定 mode 时，单任务且无重规划采用 quick，其余 full；也可在计划的 execution_mode 明确选择。

两档使用相同的状态机、消息/人工答复、需求覆盖、预算、Git 集成与报告。quick 不删除验收、确认或预算。全流程复杂度由任务需要决定，不为简单修改强制多代理。

status 返回 protocol_version、阶段、任务摘要、计划修订、预算、人工问题、交付路径和下一步。report 返回实际验收证据，不能拿 status 的计数当成通过。

旧串行 v4 快照由统一入口识别后转交 plan.skill_versions["dev-workflow"].path 指向的原冻结 runner，仅支持 run/status/cancel/clear-cancel 通用转发。其他恢复操作使用原冻结 CLI。旧协作快照仍应使用原冻结技能；不能复制新脚本进旧技能目录或修改 pins。安装升级不会迁移运行记录。
