# 额度监控与备用入口

用于用户要求无人值守、额度恢复后自动推进的**新控制器运行**。先固定用户已授权的提供商和私有认证配置；API key放在各harness自己的profile/环境中，不能写进计划、Git、命令参数或任务卡。只使用已有套餐，不能自动开余额兜底。普通Codex/Claude聊天界面的“继续”按钮不受本脚本控制。

AI准备配置，不让用户手写JSON：在worker/reviewer/coordinator原runtime加入provider_id、quota_recovery及可选fallbacks，见 [模板](../assets/runtime.provider-pool.example.json)。fallback声明id、完整harness和必要protected_files，可附additional_directories/preflight_checks；沿用原角色的工具/检查权限、时限和验收。所有入口可执行文件和profile等受保护文件在init固定摘要，不在运行中改runtime/pins。跨角色相同provider_id必须指同一入口/模型，不用API key作为身份标识。

默认60秒指数退避，上限15分钟；明确retry_after要求更久时不得提前重试。五小时、周、月额度按实际响应处理，不能用本机首次调用时间臆造重置时间。[OpenCode Go说明](https://opencode.ai/v2/docs/console/go/)包含多个额度周期；[API限流文档](https://platform.claude.com/docs/en/api/rate-limits)解释retry-after。无可靠重置时间时，退避后用原任务的真实调用探测，不伪造“额度已恢复”。

## 自动推进

调用遇明确account_quota/rate_limit后保存工作区，立即尝试尚未冷却的备用。认证、权限、代码失败和SDK美元预算耗尽不作为额度错误切换。全部受限则保存waiting_for_quota和quota_wait.retry_at；没有备用时相同。

将初始化运行交给 [Python控制服务](service.md)。服务持久保存waiting_quota/retry_at，用条件变量定时唤醒，不依赖聊天心跳、人工点击、cron或新入队事件。服务重启继续等待原时间；到期恢复同一工作区和同一实施尝试。用户取消、原有效执行预算耗尽或非额度阻塞优先停止。单独执行一次workflow.py run会在等额度时退出保留现场，需要持续推进就交给服务。

额度等待时间不消耗有效执行预算；实际调用、检查和探测耗时仍计入原预算。原单次实施时限、代码返工次数、审阅质量轮数与验收不刷新，额度探测记录另列。已有PR、候选提交和集成头保留，恢复后仍走原验收/审阅/实际合并与回归，不因切模型跳检查或重复创建PR。

## 观察和限制

workflow.py status显示等待和下次探测时间，看板显示“等额度”。每个提供商尝试有独立events/process日志，metrics分别收集，未知用量不填零，不能只统计最后成功调用。provider-cooldowns.sqlite3在运行根目录，跨角色共享、跨服务重启保留；不同run默认不共享，也没有提供商余额读取权限。

取消仍用workflow.py cancel或服务cancel事件。更换key/profile是新配置，不能覆盖冻结输入。旧运行沿用旧快照，不会因安装升级自动获得轮询；没有恢复策略的配置保持旧停止行为。固定外层截止时间仍优先，不因等额度延长。
