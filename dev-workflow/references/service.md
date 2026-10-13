# 独立服务器持续运行

服务使用 Python 3.10+ 标准库、SQLite、Git 和已配置好的 Codex/Claude CLI。它接收已准备且固定输入的运行，持久排队、启动进程、等待完成事件、保存状态；空闲时等待条件变量，不按时间重复运行任务。每个服务同时推进一个项目运行，该运行内部可并行多个分支。

## 目录与启动

建议独立服务账号 devworkflow：技能 `/opt/dev-workflow`；项目仓库/检查输入 `/srv/dev-workflow/projects`；运行 `/srv/dev-workflow/runs`；控制状态 `/srv/dev-workflow/control`。先准备目录、Git 身份、依赖及 CLI 认证，用小任务核实 CLI 可运行，再启动服务。

```text
python3 -B /opt/dev-workflow/scripts/workflow_service.py init --state-dir /srv/dev-workflow/control --runs-root /srv/dev-workflow/runs
python3 -B /opt/dev-workflow/scripts/workflow_service.py serve --state-dir /srv/dev-workflow/control
```

init 只创建服务状态和本地 auth.token，输出令牌文件路径、不输出令牌。Windows 可使用相同 CLI 和本机绝对路径，不依赖 Bash。技能安装本身不启动服务。

Linux 开机守护示例：[systemd unit](../assets/deployment/dev-workflow.service)。按实际路径、CLI PATH、认证存放位置编辑后安装。提供 [反向代理片段](../assets/deployment/nginx.conf.example)；填写自己的域名和 TLS 配置，默认服务只监听 127.0.0.1。本轮仅提供可部署文件，未连接或上线真实服务器。

## 控制协议

网页 `/` 是不含项目数据的登录入口；连接后显示 [只读运行看板](observability.md)，每 3 秒自动读取状态，也可暂停或手动刷新。支持任务/分支定位、尝试记录、事件时间线、受管理日志搜索和诊断摘要下载。令牌留在页面内存。`GET /api/runs`、`GET /api/observe...` 和 `POST /api/events` 均要求 `Authorization: Bearer <token>`。没有 URL 令牌、默认跨域授权或公开控制接口。

将以下 JSON POST 到 /api/events：

- 入队：`{"id":"unique-event-id","run_dir":"/srv/dev-workflow/runs/example"}`。
- 实际人类答复：同上再加 `response`，内容为人工答复协议的原始对象。收到答复后接续原检查点；不可让 worker 自行生成“用户已批准”。
- 停止：同上再加 `"cancel":true`。

相同事件 ID/内容幂等；相同 ID 改内容拒绝。API 只接受允许目录下已初始化、通过摘要验证的运行，不接受任意命令。准备任务仍通过统一 workflow.py 入口。控制 API 的调用者是可信操作者，Bearer token 不是模型可读取的项目资料。

## 恢复与预算

队列、派工代数、PID、人工答复与事件日志均落盘。子进程先登记派工代数再执行，旧延迟进程无法重复派工。服务重启会检查旧进程：已死且可恢复的运行使用原检查点继续；可能还活着或状态不明时标记 recovery_needed，保留现场，避免并发接管。修复环境并执行 workflow.py recover 后，用新事件 ID 入队。停止标记不会自动清除。

实现、审阅、重规划、尝试数与耗时沿用原运行预算。等待人工答复时退出控制进程，队列处于 waiting；答复入队后才唤醒。失败需先检查原因/新证据，再明确重新入队，服务不无限重试。模型账单统一硬上限尚不可用，需使用提供商配额；支持的 harness 参数仍按 runtime 配置生效。

systemd 的进程组清理用于停止该服务派生进程；应用恢复仍核实每个登记 PID。升级时停止派工，保留正在运行任务的冻结技能路径，服务本身使用新安装目录，不改旧 pins。

## 实际上线检查

确认服务账号能通过原生 CLI 完成一次小任务；验证 token 权限、运行路径白名单和代理 TLS；做一次任务中断恢复及人工答复续跑；核对分支报告和预算没有重置。操作系统沙箱、容器/文件权限和秘密隔离是部署职责：本服务和同账号工作者之间不是恶意代码安全边界。强隔离部署需将控制目录/令牌只挂载给总控，把项目只读审阅树单独挂载给 reviewer。
