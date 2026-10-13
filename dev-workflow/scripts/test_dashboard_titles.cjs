require('../assets/dashboard/i18n.js').setLocale('zh-CN');
const assert=require('node:assert/strict');
const {workflowTitle,workflowActivity,workflowGraph}=require('../assets/dashboard/dashboard.js');
const tasks=[{id:'BACKEND',module:'backend-inventory',goal:'Use Python stdlib and implement a very long detailed instruction',status:'pending',depends_on:['ROUTE_RULES']},
 {id:'ANDROID',module:'android-inventory'}, {id:'REPORT',module:'combined-report'},
 {id:'ROUTE_RULES',module:'backend-inventory',status:'running'}];
assert.deepEqual(tasks.map(t=>workflowTitle(t,tasks)),['后端接口','安卓接口','生成报告','路径规则']);
assert.equal(workflowActivity(tasks[0],tasks),'等待 路径规则');
const task={id:'X',title:'核对接口',goal:'Full exact requirements'};
assert.equal(workflowTitle(task,[task]),'核对接口');assert.equal(task.goal,'Full exact requirements');
assert.equal(workflowTitle({id:'Y',goal:'补充边界检查'}),'补充边界检查');
const duplicate=[{id:'a',title:'实现接口'},{id:'b',title:'实现接口'}];
assert.deepEqual(duplicate.map(t=>workflowTitle(t,duplicate)),['实现接口 1','实现接口 2']);
const unknown=[{id:'ONE',goal:'Long arbitrary prompt'},{id:'TWO',title:'x'.repeat(41)}];
assert.deepEqual(unknown.map(t=>workflowTitle(t,unknown)),['任务 1','任务 2']);
const before=workflowGraph(tasks);tasks[0].title='扫描后端';
assert.deepEqual(workflowGraph(tasks).edges,before.edges);
console.log('PASS: short titles, legacy labels, duplicates, full-goal preservation, dependency labels and graph identity');
