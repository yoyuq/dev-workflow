const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const {WorkflowI18n:i18n,workflowTitle,workflowActivity,workflowGraph,workflowMotionDemo,workflowRunName}=require('../assets/dashboard/dashboard.js');
test('Portable UI defaults to English and uses local preference only when actually supplied',()=>{
  assert.equal(i18n.locale(),'en');
  assert.equal(i18n.preference({cookie:'',documentElement:{dataset:{}}}),'en');
  assert.equal(i18n.preference({cookie:'',documentElement:{dataset:{preferredLocale:'zh-CN'}}}),'zh-CN');
  assert.equal(i18n.preference({cookie:'dev_workflow_locale=en',documentElement:{dataset:{preferredLocale:'zh-CN'}}}),'en');
  assert.equal(i18n.preference({cookie:'dev_workflow_locale=%ZZ',documentElement:{dataset:{}}}),'en');
});
test('Structured interpolation never translates original data or reparses braces in values',()=>{
  i18n.setLocale('en');
  assert.equal(i18n.text('{0} 缺少依赖 {1}','中文{1}','任务'), '中文{1} is missing dependency 任务');
  assert.equal(i18n.text('Unknown 中文 log'),'Unknown 中文 log');
  i18n.setLocale('zh-CN');assert.equal(i18n.text('计划 r{0}',3),'计划 r3');
});
test('Switching changes labels while preserving graph facts and original progress',()=>{
  const tasks=[{id:'A',title:'创建预约',titles:{en:'Create booking','zh-CN':'创建预约'},status:'running',activity:{text:'中文{0} / original progress'},depends_on:[]}, {id:'B',title:'Test results',status:'pending',depends_on:['A']}];
  const snapshot=JSON.stringify(tasks),edges=workflowGraph(tasks).edges;
  i18n.setLocale('en');assert.equal(workflowTitle(tasks[0],tasks),'Create booking');assert.equal(workflowActivity(tasks[0],tasks),tasks[0].activity.text);assert.equal(workflowActivity(tasks[1],tasks),'Awaiting Create booking');
  i18n.setLocale('zh-CN');assert.equal(workflowTitle(tasks[0],tasks),'创建预约');assert.equal(workflowActivity(tasks[1],tasks),'等待 创建预约');
  assert.deepEqual(workflowGraph(tasks).edges,edges);assert.equal(JSON.stringify(tasks),snapshot);
  i18n.setLocale('en');assert.equal(workflowTitle({id:'custom',title:'Custom behavior'}),'Custom behavior');
  const demo=workflowMotionDemo(0);assert.match(demo.snapshot.run_id,/Simulated/);assert.deepEqual(demo.tasks[0].checks,[]);
});
test('All marked UI keys have an English translation; static HTML starts in English',()=>{
  const src=fs.readFileSync(require.resolve('../assets/dashboard/dashboard.js'),'utf8'),markup=fs.readFileSync(require.resolve('../assets/dashboard/index.html'),'utf8');
  for(const match of src.matchAll(/T\((['"])(.*?)\1/g))assert.ok(Object.hasOwn(i18n.english,match[2]),match[2]);
  for(const match of markup.matchAll(/data-i18n(?:-aria-label|-placeholder|-title)?="([^"]+)"/g))assert.ok(Object.hasOwn(i18n.english,match[1]),match[1]);
  assert.match(markup,/<html lang="en">/);assert.match(markup,/id="language"/);
});
test('English display covers names, goals and progress without rewriting raw facts',()=>{
  i18n.setLocale('en');
  const run={goal:'原目标',snapshot:{run_id:'原项目'},display:{en:{name:{source:'原项目',text:'Original project'},goal:{source:'原目标',text:'Overall goal'}}}};
  const task={status:'running',activity:{text:'读取接口'},display:{en:{action:{source:'读取接口',text:'Inspecting API'}}}};
  const before=JSON.stringify({run,task});
  assert.equal(workflowRunName(run),'Original project');assert.equal(i18n.content(run,'goal'),'Overall goal');assert.equal(workflowActivity(task,[]),'Inspecting API');
  i18n.setLocale('zh-CN');assert.equal(workflowRunName(run),'原项目');assert.equal(i18n.content(run,'goal'),'原目标');assert.equal(workflowActivity(task,[]),'读取接口');
  assert.equal(JSON.stringify({run,task}),before);
  i18n.setLocale('en');task.activity.text='新的执行回报';assert.equal(workflowActivity(task,[]),'新的执行回报');
  assert.equal(i18n.text('查看当前会话中的阻塞说明和验证依据。'),'Review the blocker notes and verification evidence in the current session.');
});
