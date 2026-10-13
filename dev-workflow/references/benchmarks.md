# 固定案例与真实 harness 试跑

脚本测试不等于模型能力测试，单次模型成功也不证明可靠率。每次升级保存技能版本、CLI、模型配置/实际报告型号、平台、任务计划、首次产物耗时、尝试数、实际检查和未验证项。

## 可重复故障案例

```text
python -B -m unittest discover -s <WORKFLOW_ROOT>/scripts -p "test_*.py" -v
python <WORKFLOW_ROOT>/scripts/run_benchmarks.py --output <new-benchmark-dir>
```

默认使用脚本模拟 worker，但真正启动子进程、修改隔离仓库、运行断言并检查提交。固定九个场景：费用计算 bug、API 与页面联调、两任务分段恢复、运行时缺失、首次错误后根据失败反馈修复、虚报完成、只有 thinking 输出无产物、人工条件阻塞后续任务、账户额度错误停止队列。

单元/行为回归还检查越界修改、输入摘要漂移、后续任务破坏前置功能、取消恢复、部分代码显式恢复、预算不重置，以及错题范围、过期、反例与证据漂移。只检查程序可启动或 JSON 可解析不足以覆盖这些行为。

## 真实 AI

```text
python <WORKFLOW_ROOT>/scripts/run_benchmarks.py --output <new-trial-dir> --harness codex --executable <native-codex-path> --alignment <confirmed-alignment.json>
python <WORKFLOW_ROOT>/scripts/run_benchmarks.py --output <new-trial-dir> --harness claude --executable <native-claude-path> --alignment <confirmed-alignment.json>
```

真实试跑必须传入覆盖所选案例和执行限制的 [需求对齐记录](requirements-alignment.md)，不能把模拟测试的确认对象用于真实模型。会创建新的小型 Git 项目，使用当前技能快照，按 bugfix / fullstack 两个案例调用本机已配置的 CLI 和模型；可能消耗账户额度。可用 --case bugfix 只测一个。不要在过程中升级被固定摘要的技能或 CLI；完整 trial 目录包含来源和证据。

fullstack 固定检查包含真实本地 HTTP API、页面返回和 DOM 契约。它不执行浏览器 JS，也不评判界面审美；必须另外做浏览器操作/异常状态检查、视觉审查，才能声称覆盖这些维度。benchmark-results.json 明确标注模拟或真实 harness、成功/阻塞、指标和局限。模型若不报告型号/费用则记为空或继承配置，不凭空估计。

后续新增真实项目案例时覆盖：小修复、多模块功能、数据库变更、前端状态与响应式、长任务恢复。跨版本对比用同一输入与冻结验收，多次重复并列出样本数；先用结果确定改动是否有效，再增加新的工作流规则。
