# 项目深度与范围覆盖

用于完整功能和项目改造；局部修复可继续用 Markdown。项目级交互任务优先采用下面的 plan.json 为规格来源，计划书与 plan-view.json 是生成视图，session.sqlite3 保存实际进度。用户不手填这些文件。已有 PRD/计划时从其真实内容整理，保留原始引用，不制造用户确认。

## 输入约定

- `format_version: 1`；`name`、`goal`；`depth` 为 focused / feature / project，`depth_reason` 说明判定。
- `concerns`：实际涉及 frontend、backend、data_change、access、external、operations 中的哪些方面，可以为空。
- `requirements_alignment`：沿用 [真实确认记录](requirements-alignment.md)。草稿可生成计划/图，但 audit 不会显示 ready。
- `decisions`：每项有 `topic`、`status`、`answer`、`source`；status 是 answered / researched / delegated / not_applicable / open。open 另外填写 `question`、`recommendation`，回答留空。not_applicable 的 answer 写原因；delegated 的 source 指向授权。一个主题下有多个决定时用多条记录，不用一句“已确认”隐藏尚未回答的问题。
- 基础 topic：outcome、scope、acceptance、autonomy。完整功能和项目增加 journeys、baseline；项目还需 delivery。concerns 对应主题 frontend → ui_states，backend → data_contract，data_change → migration，access → access_control，external → dependency_failure，operations → operation_limits。可增加其他 topic。脚本检查的是显式主题，AI 仍需审阅实际适用性。
- `modules`：每项 `id`、`name`、`responsibility`。
- `requirements`：每项 `id`、`text`、`disposition`（implement / existing / deferred / out_of_scope）、`basis`（原始规格/用户范围决定引用）、`acceptance`（`id`、`text` 对象列表）。existing 需 `evidence` 文本列表。延期/排除保留原因与实际依据，不得由模型凭空缩小范围。
- `tasks`：每项 `id`、所选语言的简短 `title`（可选双语 `titles`）、`goal`、`module_ids`、`kind`（investigation / design / implementation / validation / review / delivery）、`depends_on`、`output`、`scope` 文本列表、`skills` 文本列表、`criterion_ids`、`verification` 文本列表。调查/设计/交付也有真实产出与检查，但不一定关联行为条件。

implement 的每个条件必须由 implementation 节点承接。focused/feature 可以在实现节点内验证；project 还要由 validation 节点关联同条件，并依赖相关实现节点。项目需审阅节点，且交付节点要等待全部本轮实现、验证和审阅，不能用报告节点掩盖未做任务。任务必须属于已有模块，任务图无环。多个需求由一个实现节点承担时提示过粗，AI 依据产出和恢复边界决定是否继续拆分；提示不是一刀切的节点配额。

## 使用

```text
python WORKFLOW_ROOT/scripts/workflow_depth.py audit --input TASK/plan.json
python WORKFLOW_ROOT/scripts/workflow_depth.py render --input TASK/plan.json --output TASK/plan.md
python WORKFLOW_ROOT/scripts/workflow_depth.py view --input TASK/plan.json --output TASK/plan-view.json
python WORKFLOW_ROOT/scripts/workflow_session.py init --session-dir TASK/view --input TASK/plan-view.json
```

audit 返回所有结构遗漏、主题下一步、条件到任务的对应及大节点提示；ready=false 退出码 2。render/view 可以输出结构有效的草稿，不等于授权开工或自动验证。文件只新建，不覆盖现有文档；修订时生成新版本并记录依据。图节点沿用真实任务 ID，完整目标含输出、条件 ID 与检查；不会虚构状态或完成证据。

已有会话用原 session 的 add/dependencies 更新，不重新 init，也不把旧节点清空。计划修订与 session patch 一起保存；完成节点保留，未完成需求不能静默降级成 existing/deferred。最多 64 个可见节点，超过时按真实阶段准备后续细化，保留未完成范围和阶段入口，不截断需求。

## 交付复核

audit ready 仅表示计划结构就绪，不是任务完成。最后从当前 session 和实际检查收集“需求 → 条件 → 实现节点 → 验证证据 → 当前结果”，existing 的旧依据在代码变化后核对是否仍有效；延期和待人工判断分别报告。与原始请求复核，防止源清单漏了一整条诉求。检查脚本无法辨别伪造的用户原话、测试质量或业务语义，这些必须由 AI 的规格审阅和真实执行验证负责。

夜间 v2/v4 沿用其固定准备数据、覆盖检测和独立验收；不要直接把此交互格式提交给控制器。工作包中的细步骤要体现在任务卡/回报中，只有实际可独立合并的工作包才成为分支任务。
