require('../assets/dashboard/i18n.js').setLocale('zh-CN');
const test=require('node:test');
const assert=require('node:assert/strict');
const {workflowGraph,workflowCounts,workflowNodeMotion,workflowEdgeFlow,workflowMotionDemo}=require('../assets/dashboard/dashboard.js');

test('Only active execution states animate; terminal runs suppress stale active tasks',()=>{
  for(const status of ['running','starting','creating','committing'])assert.equal(workflowNodeMotion(status,'executing'),true);
  for(const status of ['pending','blocked','failed','completed','integrated','awaiting_review'])assert.equal(workflowNodeMotion(status,'executing'),false);
  for(const phase of ['completed','failed','cancelled'])assert.equal(workflowNodeMotion('running',phase),false);
});
test('Direction follows completed prerequisites into active work, never a pending successor',()=>{
  const tasks=[{id:'A',status:'running'},{id:'B',status:'pending'}];
  assert.equal(workflowEdgeFlow({from:'@goal',to:'A'},tasks,'executing'),true);
  assert.equal(workflowEdgeFlow({from:'A',to:'B'},tasks,'executing'),false);
  tasks[0].status='completed';tasks[1].status='running';
  assert.equal(workflowEdgeFlow({from:'A',to:'B'},tasks,'executing'),true);
  tasks[0].status='blocked';assert.equal(workflowEdgeFlow({from:'A',to:'B'},tasks,'executing'),false);
  assert.equal(workflowEdgeFlow({from:'missing',to:'B'},tasks,'executing'),false);
  assert.equal(workflowEdgeFlow({from:'@goal',to:'missing'},tasks,'executing'),false);
});
test('Milestone dependencies use the same status truth as task dependencies',()=>{
  const tasks=[{id:'schema',status:'integrated',produces:['contract']},{id:'api',status:'creating',requires:['contract']}];
  const edge=workflowGraph(tasks).edges.find(e=>e.kind==='milestone');
  assert.equal(workflowEdgeFlow(edge,tasks,'executing'),true);
  assert.equal(workflowEdgeFlow(edge,tasks,'completed'),false);
});
test('Six-step demo is labelled, isolated and contains no real process, checks or commit',()=>{
  const first=workflowMotionDemo(0),copy=JSON.stringify(first);
  for(let i=0;i<6;i++){
    const d=workflowMotionDemo(i),layout=workflowGraph(d.tasks);
    assert.equal(d.demo,true);assert.match(d.snapshot.run_id,/模拟/);
    assert.deepEqual(layout.warnings,[]);
    for(const t of d.tasks){assert.deepEqual(t.processes,[]);assert.deepEqual(t.checks,[]);assert.equal(t.commit,undefined);assert.match(t.evidence[0],/没有运行/);}
  }
  assert.equal(JSON.stringify(first),copy);
  first.tasks[0].status='failed';assert.equal(workflowMotionDemo(0).tasks[0].status,'running');
});
test('Replan adds support before backend; completion stops every edge',()=>{
  const start=workflowMotionDemo(0),replan=workflowMotionDemo(1),active=workflowMotionDemo(3),done=workflowMotionDemo(5);
  assert.equal(workflowCounts(start.tasks).running,2);
  assert.equal(replan.tasks.find(t=>t.id==='DEMO_BACKEND').status,'blocked');
  assert.equal(replan.snapshot.revision,1);
  const edge=workflowGraph(active.tasks).edges.find(e=>e.from==='DEMO_RULES'&&e.to==='DEMO_BACKEND');
  assert.equal(workflowEdgeFlow(edge,active.tasks,active.snapshot.phase),true);
  assert.equal(workflowCounts(done.tasks).done,done.tasks.length);
  assert.ok(workflowGraph(done.tasks).edges.every(e=>!workflowEdgeFlow(e,done.tasks,done.snapshot.phase)));
});
