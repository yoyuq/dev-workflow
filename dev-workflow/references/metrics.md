# 真实运行的耗时、用量与人工介入

运行中或结束后执行 `python WORKFLOW_ROOT/scripts/workflow.py metrics --run-dir RUN`，输出只读 JSON。读取控制器保存的模型进程记录、SDK 结束事件和人工答复记录，不派工、不调用模型、不读取供应商账单，也不输出原始模型消息/推理。

- `processes` 分列 worker、reviewer、coordinator 的日志来源、退出结果、已知耗时和 SDK usage；模拟/unknown 适配器不计入配置为模型的进程统计。
- `usage_totals_by_format` 保留 Codex/Claude SDK 各自字段。Codex 的 cached_input_tokens 是输入中的缓存部分，不再加一次；reasoning_output_tokens 同样单列，不另加到 output_tokens；不同格式不混成同一种账单用量。
- `usage_coverage` 明确缺少最终 usage 的进程。真实中断可能没有 process.json 或 turn.completed；已知 token 合计只是下界，不能用缺失值补零。
- `money.actual_invoice_total` 当前为 null。SDK 的 reported cost 如果存在，单独统计并列出覆盖率；不等于实际账单，也不把订阅套餐下的调用称为免费。宿主聊天本身的用量不在这些进程日志中，不能声称已包含。
- 进程耗时之和包含并行重叠。实际控制器墙钟时间及预算仍看 `status` / `delivery.json`；强制中断的未完成进程耗时为未知，故障注入的操作时间另记。
- `human_response_records` 是人工答复协议中的记录数，不能证明回复真由人提供。宿主救援、临时改计划、手改业务代码和环境修复需写独立操作日志；没有日志时 `host_rescue_actions` 为 null，不能声称零介入。

真实案例应提前定义指标和故障注入点。分开记录：初始准备、已安排的故障注入/恢复、自动模型返工、实际用户新增决定、宿主未预设救援。原运行失败后另建输入/新版本的补测单列，不将补测成功改写为首轮无人介入成功。可公开汇总、提交和可复现方法，私人路径、原始模型输出、令牌和用户数据留在本地。
