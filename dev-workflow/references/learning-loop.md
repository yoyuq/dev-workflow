# 错题本闭环

协调控制器为失败尝试保存 failure_cases：运行、任务、尝试、执行环境、失败检查及日志副本。任务卡向后续工作者提供案例 ID 和证据。最初是 unresolved，不推测根因。

协议/运行时失败另外保留稳定症状、文件通道回执、工作者能力报告与验收交接。下一次任务卡直接携带诊断和调整建议；同类失败再次出现需通过 [重试门槛](worker-protocol.md)。这些运行证据用于指导恢复，不自动满足下面的代码经验提升条件。

工作者有证据时可在自身 `.workflow-mailbox/learning.json` 提交：

```json
{"diagnoses":[{"case_id":"case-ID","root_cause":"根因与依据","fix":"实际修复","prevention":"仅在此条件下适用的经验","check_ids":["behavior"]}],"feedback":[{"lesson_id":"实际检索到的经验ID","outcome":"helped","note":"实际采用情况及证据"}]}
```

反馈 outcome 为 helped/not_applicable/contradicted；未采用就不写 helped。诊断与使用反馈都是模型提议，不能自授 validated。

修复并集成后，诊断先记录 candidate。同时具备以下条件才提升 validated：同案例确实存在指定的外部失败检查；最终相同检查通过；整个运行完成验收；独立审阅报告对这条诊断明确给出 supported。仅实现通过、重新退出 0 或 worker 自称原因正确都不足够。没有 reviewer 的 quick 任务可积累候选，之后再补证据。

提升时复制前后日志、代码提交、诊断和独立报告至错题本，默认限定当前项目、harness、版本和系统。共享记忆请为各运行配置同一 memory_dir 和稳定 memory_project。仅复用同目录不意味着所有项目适用；范围匹配仍然执行。

下一次任务卡包括被检索经验 ID 与推荐资格，完成后写入实际使用反馈。矛盾、过期、证据漂移会使经验退出自动建议；helped 不会提升未经验证的候选。模型审阅仍非数学证明，经验保留适用范围和限制，不自动写成永久全局规则。
