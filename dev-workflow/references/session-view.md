# 在普通对话中显示流程图

适用于当前 Codex / Claude Code 会话直接实施的开发任务，不需要独立控制器。使用随包 `scripts/workflow_session.py` 记录实际计划、动作和验证依据，复用看板。它只显示 AI 明确记录的公开进度，不监听聊天、推理或编辑器，也不自动判断代码完成。

## 开始时

沿用当前任务计划和 ID，复用已有 session.sqlite3；没有记录时，在任务输出目录下创建 `view/`。普通计划默认 `.workflows/<任务短名>/`，仓库不允许辅助文件时用宿主工作目录。不要把凭据或会话记录写进 Skill 安装目录或 Git 提交。

用文件工具编写 plan-view.json，节点与实际计划一致；简短任务可以只有一个节点，不要套固定的前后端步骤。项目级任务按 [plan-depth.md](plan-depth.md) 从同一份计划生成图：模块是分组，具体行为、必要准备、联调、回归与审阅才是节点。包内 [课程预约示例](../assets/project-plan.example.json) 有 18 个实际工作单元，仍是未确认草稿，用来说明拆解，不能原样启动开发。

```json
{"name":"登录功能","goal":"实现已确认的登录功能","tasks":[
  {"id":"api","title":"校验登录","goal":"实现登录校验和拒绝错误密码，运行相关接口用例","module":"后端","depends_on":[]},
  {"id":"ui","title":"登录表单","goal":"接入登录并显示输入和接口错误，完成浏览器操作检查","module":"前端","depends_on":["api"]}
]}
```

```text
python WORKFLOW_ROOT/scripts/workflow_session.py init --session-dir TASK/view --input TASK/plan-view.json
python WORKFLOW_ROOT/scripts/workflow_session.py open --session-dir TASK/view
```

`open` 仅启动本机只读预览，不派工、不调用模型、不创建 Git 分支。立即使用返回的 url：Codex 桌面优先 `open_in_codex` 的 browser target，在当前对话右侧打开；已有面板不重复打开。若 open_in_codex 返回 queued，仅代表排队；当前宿主有内置浏览器工具时，使用它创建可见标签页并确认已连接、节点可见，最后标记为 deliverable 保留。否则如实说明需要切回当前对话查看，连接码过期时重新 open。其他宿主用其浏览器预览能力；只有可用外部浏览器时可打开外部浏览器。宿主不支持打开时给出链接并明确说明尚未展示，不能只说“已打开”。

URL 片段中的 connect 是 120 秒有效的一次性连接码，页面连接后立即清除片段，并使用 HttpOnly、SameSite=Strict 的浏览器会话 Cookie 保持连接；不把该链接写进项目、报告或最终交付。同一浏览器中的刷新、返回首页和新标签页会恢复连接，不要求手填令牌。其他浏览器、主动断开、服务重启或连接超过 8 小时后，重新 `open` 获取入口；普通定位链接不授予新浏览器访问权限。不要输出或分享 viewer.json 中的令牌。

新节点使用所选语言的简短 `title`（中文 2–12 字、英文 2–40 字符），`goal` 保留完整要求。可选 `titles` 保存 en / zh-CN 标签。看板右上角可切换语言并记住浏览器选择；初始语言和新任务输出遵循 [language.md](language.md)。原文日志、目标与回报保留原语言。标题和语言不改变任务 ID、依赖和验收。

## 工作中

开始一个节点、当前动作改变、验证结束、遇到阻塞或计划调整时，用简短 patch.json 更新；不要每次工具调用都登记。首次 expected_sequence 为 0，每次成功更新增加 1。续接或提示过期时先 `status` 读取当前序号，不盲目覆盖。

```json
{"expected_sequence":0,"updates":[
  {"id":"api","status":"running","action":"实现账号校验"}
]}
```

```text
python WORKFLOW_ROOT/scripts/workflow_session.py update --session-dir TASK/view --input TASK/patch.json
python WORKFLOW_ROOT/scripts/workflow_session.py status --session-dir TASK/view
```

允许状态 pending/running/blocked/failed/completed。completed 必须有非空 evidence 文本列表，且前置节点均已完成。记录实际命令、结果或审查依据，不能把写了文件当成通过验收；脚本只检查记录结构，不能独立证明证据真实。重试切换回 running 会增加尝试次数并清除旧验证依据。

```json
{"expected_sequence":1,"updates":[
  {"id":"api","status":"completed","evidence":["在项目目录运行 pytest tests/test_login.py：8 passed"]},
  {"id":"ui","status":"running","action":"接入登录接口"}
]}
```

实际实施路径变化时，在原目标范围内提供 `add` 新节点和/或 `dependencies`（任务 ID → 完整前置列表），并填写 reason。变化生成计划修订、更新图的节点和边；不能删除或重写已完成节点。已完成工作之外的新需求另加后续任务；改变总目标要重新与用户对齐。

```json
{"expected_sequence":2,"reason":"接口验证发现缺少边界样例，先补齐再联调",
 "add":[{"id":"cases","goal":"补充边界样例","depends_on":["api"]}],
 "dependencies":{"ui":["api","cases"]},
 "updates":[{"id":"ui","status":"pending"},{"id":"cases","status":"running","action":"添加空输入样例"}]}
```

页面每 3 秒重读记录，显示“会话回报”和最后回报时间；AI 未更新记录时图不会自行改变。不要声称有宿主级 hook 或自动监听。

## 限制与退出

最多 64 节点、100 次计划修订，最近 200 条更新事件。验证依据直接显示为纯文本，不开放任意日志文件读取。完整进程日志仍属于独立控制器模式。用户拒绝预览、Python 或本机端口不可用时，保留 Markdown 计划并在聊天给简短进度；明确说预览不可用，不为看图安装全套服务或改权限。

预览只绑定 127.0.0.1，无浏览请求 10 分钟后退出；浏览器打开时会持续读取。`close --session-dir TASK/view` 只关闭对应预览，不停止当前开发任务。结束任务时保留可查看的记录和必要报告。旧会话不会自动补造历史；按真实当前计划登记后继续。
