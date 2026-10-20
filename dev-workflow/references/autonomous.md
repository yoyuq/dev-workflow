# 独立运行：按需展开

用户要求独立进程、服务器、夜间执行或可恢复的分支协作时使用本入口；当前会话中的普通开发只需要主入口和当前任务指南。

1. 先复用真实确认，再按 [需求对齐](requirements-alignment.md)、[planning](planning.md) 和 [entrypoint](entrypoint.md) 准备本次范围、任务、固定验收、预算、停止条件与交付终点。配置由 AI 准备，不让用户手填 JSON。`setup` 生成配置，`doctor` 做离线检查，`doctor --live` 才实际调用模型。
2. 单任务用 quick；多模块协作、受控重规划或完整独立审阅用 full，full 必须有 reviewer。按 [coordination](coordination.md) 管理分支、消息、共享契约和依赖；任务内前端默认使用 [frontend](frontend.md)。
3. 只为需要的能力读取下表。原验收、授权和预算不刷新，历史运行保留冻结版本与 pins。普通会话不需要依次配置整张表。

| 需要的能力 | 准备与执行规则 |
|---|---|
| 服务器常驻、持久队列 | [service](service.md)：安装不启动服务；使用独立状态目录和受限接口 |
| 主入口受限后换备用、全部受限后续跑 | [provider-recovery](provider-recovery.md)：事先固定入口和 quota_recovery，持久化冷却并续接原尝试；认证/代码错误单独处理 |
| 逐功能创建 PR、测试、合并和失败回退 | [feature-prs](feature-prs.md)：总控拥有远程操作；实际合并后回归，失败按真实授权普通 revert 并修复；最终基线 PR 保留待审 |
| 按复杂度、依赖与风险拆分功能 | [task-partitioning](task-partitioning.md)：原子边界和完整父任务验收保留，预算不足不激活拆分 |
| 运行中调整实施路径 | [replanning](replanning.md)：在原目标内版本化调整，不扩大范围或改原验收 |
| 需要真实人类答复 | [human-resume](human-resume.md)：保存具体问题；已有授权任务可继续，AI 不代替用户确认 |
| 有限自动检查与审阅恢复 | [automatic-assurance](automatic-assurance.md)：固定检查与负责人，原预算和批准条件不放宽 |
| 查看运行与排错 | [observability](observability.md)：实际任务图、时间线、受管理日志；现象不代替根因 |
| 失败经验与跨会话续接 | [learning-loop](learning-loop.md) / [memory](memory.md)：保留原证据，验证后提升经验 |
| 记录耗时、模型用量与介入 | [metrics](metrics.md)：用 metrics 只读汇总，未知与中断缺失保持未知 |

派工前核验真实环境与固定输入；`self-test` 的模拟工作者只证明控制流程，不能代替原生模型、浏览器或项目验收。工单沿用原需求，worker 不重新规划整个项目或自行推送/合并。宿主、用户授权和停止请求始终有效。
