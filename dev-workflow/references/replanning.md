# 受控重规划

协作计划可以预先声明 `replanning: {max_revisions, max_added_tasks, max_total_attempts, preparation_checks}`。没有该策略的快照不启用重规划。准备检查采用普通检查格式，必须在原计划里固定，和最终验收一起校验、摘要保护。

实施修订只支持：在现有任务范围内新增准备任务，以及改变未验收任务的 `depends_on`。准备任务指定 `id,parent,goal,allowed_scope,checks,depends_on`；checks 是 preparation_checks 的 ID。准备任务先通过自己的检查，父任务仍需通过原来的完整验收。新增准备任务自动成为父任务依赖，不能移除这个边。

提交结构：`{id, expected_revision, reason, add: [...], dependencies: {TASK: [DEPENDENCY]}}`。使用 `workflow_coordinator.py replan --run-dir RUN --proposal FILE`，或 AI 协调者提交单独的 replan 决策。总控在没有运行中工作者、未提交成果或待签收候选的检查点激活。被阻塞任务可以在保持尝试记录的前提下调整实施依赖；必要人类决定必须先解决。

2.11.2 会保留等待中的提案，先完成当前并行成果的验收、审阅和集成，再应用修订并派发下一批任务。暂未到达可应用的检查点不会被当成提案无效；等待人工签收或恢复时也不丢弃提案。

修订存储在同一 SQLite 事务的事实状态和事件中，包含序号、前版本摘要、提案及新摘要。读取时从固定原计划重放并检查，不修改 plan.json/pins。已有任务的验收、文件范围、需求、授权、预算均不能通过这种协议改写；已集成任务定义也不能改。新增任务受总量和全局尝试数限制，时间预算沿用原运行。

总目标或产品决定改变时回到 grill-me 准备新目标。准备检查只证明该准备步骤，不能代替原验收。模型仍需用 reason 和项目证据说明拆分合理性；结构校验无法证明业务语义。
