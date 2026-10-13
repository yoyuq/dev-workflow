# 工作者预检、交接与失败诊断

本协议用于新建的协调运行。旧运行继续使用自己的冻结 Skill。Python 控制器必须能执行；工作者读写消息可以只使用宿主文件工具。

## 开工时的能力预检

原生 Codex / Claude 工作者首先读取自身 `.workflow-mailbox/inbox.json`，确认本次 assignment 及已读消息，然后提交 capabilities。先做这几步，再读取长指南和编写代码。控制器收到合法 outbox 和本次 assignment 的回执，才把文件消息通道记为已验证。默认限时 90 秒，受单次上限约束；runtime.worker 可用正整数 `worker_preflight_seconds` 调整。自定义 command 适配器需显式配置此字段才能启用强制预检及交接。

直接用文件工具在自身 `outbox/` 新建 UTF-8 JSON；使用原子编辑或临时文件改名，唯一 ID，消费后不得改写：

```json
{"id":"ack-1","type":"ack","to":"coordinator","ids":["真正读到的消息ID"]}
```

```json
{"id":"cap-1","type":"capabilities","to":"coordinator","data":{"transport":"files","local_checks":"not_probed","evidence":"文件读写可用，项目命令尚未探测。"}}
```

`transport` 为 files/helper；`local_checks` 为 available/unavailable/not_probed。命令能力是工作者报告，不能充当成功探针或授予权限；宿主 preflight_checks 的成功也不能证明沙箱内工具可用。只对任务确实需要、且已允许的工具做最小实际探测。Python 消息助手是可选便利工具；被拒绝后改用文件通道。浏览器和最终验收环境仍须由实际外部检查验证。

预检超时会停止本次进程并保留现场；协议是合作式约定，不是操作系统隔离，也不保证恶意工作者不会提前改文件。

## 从实现移交给验收

实现完成且没有未决需求/依赖后，重新读取并确认消息，提交以下信号并及时退出：

```json
{"id":"ready-1","type":"ready_for_verification","to":"coordinator","data":{"implementation":"ready","local_checks":"unavailable","evidence":"完成的文件及行为；本地检查无法执行的实际原因。"}}
```

`local_checks` 为 passed/failed/unavailable/not_run。任一值都不能替代控制器验收。控制器要求进程正常退出、预检完成、必需消息已确认、无未决问题、契约未失效、实际修改在范围内，然后运行原有固定检查、提交及独立审阅。ready 后继续改坏的文件也会检查；超时、取消、失败进程不会因曾发 ready 而被接受。

`blocked` 仅表示实现仍缺必要前提。工作者不能运行 Python 或浏览器，但实现已备妥时，使用交接信号。同时报告 blocked 和 ready 时，未解决的阻塞仍然有效。控制器不读取文字猜测“这次 blocked 大概已完成”，也不把旧运行的失败改为成功。

## 同类故障的重试门槛

失败记录保存稳定症状签名、尝试、旧工作区、失败检查、进程、预检及交接证据。下一张任务卡直接呈现诊断线索和建议变化，不依赖已经失效的消息助手去取反馈。任务卡精简同伴与历史内容，完整证据保留在原路径。

第一次失败可在原预算内修正；同一签名再次出现时停止自动原样重试，其他可运行任务继续。AI 协调者若提出 retry，须提供当前 `retry_gate` 对应的 `changed_approach`：failure_signature、case_id、diagnosis、strategy。重复相同策略、旧签名或无证据引用会被拒绝。控制器校验引用和重复项，策略是否合理仍需模型判断及后续验收，并非语义正确性的证明。

实际用户答复可以解除门槛，答复来源与原门槛一并留档；不能代答。重试、人工答复、重规划都不重置尝试和时间预算。原状态、失败案例及草稿保留。预检成功只说明协议通道恢复，故障根因仍可为未验证；错题本不会因此自动把它提升为通用经验。
