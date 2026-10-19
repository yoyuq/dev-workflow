'use strict';
const WorkflowI18n=typeof module!=='undefined'&&module.exports?require('./i18n.js'):window.WorkflowI18n;
const T=(key,...args)=>WorkflowI18n.text(key,...args);
// Deterministic layout: both dependency lists and milestone producers are facts.
function workflowGraph(tasks, vertical=false) {
  const ids=new Set(tasks.map(t=>t.id)), producers=new Map(), warnings=[];
  for(const t of tasks)for(const name of t.produces||[])producers.set(name,t.id);
  const parents=new Map(tasks.map(t=>[t.id,new Set()])), edges=[];
  for(const t of tasks) {
    const dependencies=new Map((t.depends_on||[]).map(id=>[id,'task']));
    for(const name of t.requires||[]) {
      const owner=producers.get(name);
      if(owner) {if(!dependencies.has(owner))dependencies.set(owner,'milestone');}
      else warnings.push(T("{0} 缺少里程碑 {1}" , t.id, name));
    }
    for(const [id,kind] of dependencies) {
      if(!ids.has(id)){warnings.push(T("{0} 缺少依赖 {1}" , t.id, id));continue;}
      parents.get(t.id).add(id);edges.push({from:id,to:t.id,kind});
    }
  }
  const levels=new Map(), remaining=new Set(ids);
  while(remaining.size) {
    const ready=[...remaining].filter(id=>[...parents.get(id)].every(p=>levels.has(p)));
    if(!ready.length){warnings.push(T('检测到依赖循环'));let last=Math.max(0,...levels.values())+1;for(const id of remaining)levels.set(id,last++);break;}
    for(const id of ready){levels.set(id,Math.max(-1,...[...parents.get(id)].map(p=>levels.get(p)))+1);remaining.delete(id);}
  }
  const groups=[];for(const t of tasks){const rank=levels.get(t.id)||0;(groups[rank]??=[]).push(t.id);}
  const count=Math.max(1,...groups.map(g=>g?.length||0)), w=vertical?140:190,h=122,gap=vertical?20:44,step=vertical?174:232;
  const breadth=count*(vertical?w:h)+(count-1)*gap+(vertical?24:40), depth=(groups.length+1)*step+(vertical?h:w)+40;
  const width=vertical?breadth:depth,height=vertical?depth:Math.max(300,breadth),nodes=[];
  function place(id,rank,index,total,kind='task') {
    const cross=(vertical?width:height)/2-total*(vertical?w:h)/2-(total-1)*gap/2+index*((vertical?w:h)+gap);
    nodes.push({id,kind,w,h,x:vertical?cross:20+rank*step,y:vertical?20+rank*step:cross});
  }
  place('@goal',0,0,1,'goal');
  groups.forEach((g,rank)=>(g||[]).forEach((id,i)=>place(id,rank+1,i,g.length)));
  place('@delivery',groups.length+1,0,1,'delivery');
  const hasChild=new Set(edges.map(e=>e.from));
  for(const t of tasks){if(!parents.get(t.id).size)edges.push({from:'@goal',to:t.id,kind:'root'});if(!hasChild.has(t.id))edges.push({from:t.id,to:'@delivery',kind:'end'});}
  if(!tasks.length)edges.push({from:'@goal',to:'@delivery',kind:'empty'});
  return {nodes,edges,width,height,warnings,vertical};
}
function workflowCounts(tasks) {
  return {total:tasks.length,done:tasks.filter(t=>['integrated','completed'].includes(t.status)).length,
    running:tasks.filter(t=>t.status==='running').length,
    attention:tasks.filter(t=>['failed','blocked','awaiting_review'].includes(t.status)).length};
}
function workflowTitle(task,tasks=[]) {
  const compact=value=>typeof value==='string'&&value===value.trim()&&[...value].length>=2&&[...value].length<=(/[\u4e00-\u9fff]/u.test(value)?12:40)&&!/[\x00-\x1f\x7f\u2028\u2029]/u.test(value);
  function base(t) {
    const localized=t.titles?.[WorkflowI18n.locale()];
    if(compact(localized))return localized;
    if(compact(t.title))return T(t.title);
    if(compact(t.goal)&&/[\u4e00-\u9fff]/u.test(t.goal))return T(t.goal);
    // Compatibility for old English plans; never display a clipped worker prompt.
    for(const value of [t.id,t.module]) {
      const words=String(value||'').toLowerCase().split(/[^a-z]+/);
      const has=word=>words.includes(word);
      if(has('route')&&has('rules'))return T('路径规则');
      if(has('android'))return T('安卓接口');
      if(has('backend'))return T('后端接口');
      if(has('frontend'))return T('前端界面');
      if(has('report'))return T('生成报告');
      if(has('test')||has('tests'))return T('验证结果');
      if(has('review'))return T('独立审阅');
      if(has('docs'))return T('补充文档');
    }
    if(compact(t.module))return T(t.module);
    return T('任务');
  }
  const label=base(task),peers=tasks.filter(t=>base(t)===label);
  return label===T('任务')||peers.length>1?label+' '+Math.max(1,peers.findIndex(t=>t.id===task.id)+1):label;
}
function workflowRunName(run) {const raw=run.snapshot?.run_id??run.run_id;return WorkflowI18n.content(run,'name',WorkflowI18n.content(run,'run_id',raw));}
function workflowActivity(task,tasks) {
  if(task.status==='running')return WorkflowI18n.content(task.activity,'text',WorkflowI18n.content(task,'action',task.activity?.text))||T('执行中 · 等待进度回报');
  if(task.status==='pending') {
    const ids=new Set(task.depends_on||[]);
    for(const name of task.requires||[])for(const t of tasks)if((t.produces||[]).includes(name))ids.add(t.id);
    const waiting=[...ids].filter(id=>!tasks.some(t=>t.id===id&&['integrated','completed'].includes(t.status)));
    return waiting.length?T('等待 ')+waiting.map(id=>workflowTitle(tasks.find(t=>t.id===id)||{id},tasks)).join(T('、')):task.attempts?T('等待重试'):T('等待调度');
  }
  return {quota_wait:T('等待额度恢复后自动续跑'),quota_resume:T('恢复任务'),creating:T('准备工作分支'),finished:T('等待验收检查'),verified:T('等待审阅与集成'),committing:T('记录检查结果'),awaiting_review:T('等待验收确认'),integrated:T('结果已集成'),completed:T('结果已完成'),failed:T('检查失败 · 查看详情'),blocked:T('任务受阻 · 查看详情'),cancelled:T('已停止执行')}[task.status]||T('查看执行详情');
}
function workflowNodeMotion(status,phase) {
  return !['completed','failed','cancelled'].includes(phase)&&['running','starting','creating','committing'].includes(status);
}
function workflowEdgeFlow(edge,tasks,phase) {
  // Motion points into work actually executing, never through a waiting dependency.
  const target=tasks.find(t=>t.id===edge.to),source=tasks.find(t=>t.id===edge.from);
  return workflowNodeMotion(target?.status,phase)&&(edge.from==='@goal'||['integrated','completed'].includes(source?.status));
}
function workflowMotionDemo(step) {
  const index=((step%6)+6)%6,at=Date.now()/1000;
  const make=(id,title,status,depends_on=[],text='')=>({id,title,goal:T('模拟任务：')+title,status,depends_on,requires:[],produces:[],attempts:status==='pending'?0:1,checks:[],processes:[],failure_cases:[],evidence:[T('仅用于预览动效；没有运行模型、测试或提交。')],activity:text?{text,at,attempt:1}:null,next_step:T('演示下一步会补充依赖后重试。')});
  const frontend=make('DEMO_FRONTEND',T('前端界面'),index===0?'running':'completed',[],T('编写页面与交互'));
  const backend=make('DEMO_BACKEND',T('后端接口'),index===0||index===3?'running':index===1?'blocked':index===2?'pending':'completed',index>0?['DEMO_RULES']:[],T('编写接口与边界处理'));
  const report=make('DEMO_REVIEW',T('核对结果'),index===4?'running':index===5?'completed':'pending',['DEMO_FRONTEND','DEMO_BACKEND'],T('核对结果与验收依据'));
  const tasks=[frontend,backend,report];
  if(index>0){const rules=make('DEMO_RULES',T('补充约定'),index===1?'pending':index===2?'running':'completed',[],T('统一前后端字段约定'));rules.introduced_revision=1;rules.support_for='DEMO_BACKEND';tasks.splice(1,0,rules);}
  return {source:'interactive',demo:true,demo_step:index,goal:T('并行开发，补充依赖，再完成验收'),reported_at:at,tasks,snapshot:{run_id:T('动效演示 · 模拟工作流'),phase:index===5?'completed':'running',revision:index>0?1:0,budget:{elapsed_seconds:0,remaining_seconds:0},human_requests:[]},planning:{active:false,pending:false},plan_changes:index>0?[{revision:1,reason:T('模拟：后端反馈需要统一字段，补充准备任务。'),added:['DEMO_RULES'],dependencies:[{task:'DEMO_BACKEND',before:[],after:['DEMO_RULES']}]}]:[],artifacts:[],timeline:[],messages:[]};
}
if(typeof module!=='undefined'&&module.exports)module.exports={WorkflowI18n,workflowGraph,workflowCounts,workflowActivity,workflowTitle,workflowRunName,workflowNodeMotion,workflowEdgeFlow,workflowMotionDemo};
else (()=>{
  const $=id=>document.getElementById(id), ns='http://www.w3.org/2000/svg';
  const labels=new Proxy({waiting_for_quota:'等额度',waiting_quota:'等额度',quota_wait:'等额度',quota_resume:'恢复任务',pending:'待执行',running:'执行中',starting:'启动中',creating:'准备中',committing:'记录结果',queued:'排队中',completed:'已完成',integrated:'已集成',verified:'检查通过',finished:'已实现',failed:'失败',blocked:'受阻',waiting:'待答复',waiting_human:'待答复',awaiting_review:'待验收',cancelled:'已停止',recovery_needed:'需要恢复',partial:'部分完成',passed:'通过'},{get:(map,key)=>T(map[key])});
  const localViewer=document.body.dataset.sessionView==='true';
  let sessionConnecting=false;
  const state={token:'',connected:false,epoch:0,busy:false,runs:[],run:'',task:'',panel:'',detail:null,artifact:'',log:null,detailRequest:0,logRequest:0,filter:'all',known:new Map(),notifications:false,demo:null};
  const reducedMotion=matchMedia('(prefers-reduced-motion:reduce)');
  let motionEnabled=!reducedMotion.matches,motionOverride=false,graphVisible=true,graphRun='',inspectorKey='',demoAt=0;
  const transitions=new Map();
  function motionAllowed(){return motionEnabled&&graphVisible&&(!reducedMotion.matches||motionOverride)&&!document.hidden;}
  function stopTransition(node){const a=transitions.get(node);if(a){a.cancel();transitions.delete(node);}}
  function transition(node,frames,duration=260){stopTransition(node);if(!motionAllowed()||!node.animate)return;const a=node.animate(frames,{duration,easing:'cubic-bezier(.16,1,.3,1)'});transitions.set(node,a);a.finished.catch(()=>{}).finally(()=>{if(transitions.get(node)===a)transitions.delete(node);});}
  function syncMotion(){document.body.dataset.motion=motionAllowed()?'on':'off';document.body.dataset.motionOverride=String(motionOverride);$('motion-toggle').setAttribute('aria-pressed',String(motionEnabled));$('motion-toggle').textContent=motionEnabled?T('暂停动效'):T('开启动效');$('motion-toggle').title=reducedMotion.matches&&!motionOverride?T('系统已减少动态；点击后只在本页面启用动效'):T('只影响视觉动效，不影响任务执行');if(!motionAllowed())for(const n of [...transitions.keys()])stopTransition(n);demoAt=Date.now();}
  $('motion-toggle').onclick=()=>{motionEnabled=!motionEnabled;motionOverride=motionEnabled&&reducedMotion.matches;syncMotion();};reducedMotion.addEventListener('change',()=>{motionOverride=false;motionEnabled=!reducedMotion.matches;syncMotion();});syncMotion();
  const motionVisibility=new IntersectionObserver(entries=>{for(const entry of entries)entry.target.dataset.inView=String(entry.isIntersecting);});
  new IntersectionObserver(entries=>{const visible=entries[0].isIntersecting;if(visible!==graphVisible){graphVisible=visible;syncMotion();}}).observe($('graph-viewport'));
  const tone=status=>['integrated','completed','passed'].includes(status)?'good':['running','starting','creating','committing'].includes(status)?'active':['failed','blocked','recovery_needed'].includes(status)?'error':['quota_wait','quota_resume','waiting_quota','waiting_for_quota','awaiting_review','waiting','waiting_human','partial'].includes(status)?'warn':'idle';
  const attention=t=>['failed','blocked','awaiting_review'].includes(t.status);
  function el(tag,text='',cls=''){const n=document.createElement(tag);n.textContent=text;if(cls)n.className=cls;return n;}
  function svg(tag,attrs){const n=document.createElementNS(ns,tag);for(const [key,value] of Object.entries(attrs))n.setAttribute(key,String(value));return n;}
  function icon(kind){const n=svg('svg',{viewBox:'0 0 24 24','aria-hidden':'true'});const paths={good:'m5 12 4 4L19 6',active:'m9 5 10 7-10 7Z',error:'M12 5v8m0 4v1',warn:'M8 6v12m8-12v12',idle:'M5 12h14',goal:'M5 7h14M5 12h10M5 17h6',delivery:'M6 20V4m0 1h12l-3 4 3 4H6'};n.append(svg('path',{d:paths[kind]||paths.idle}));return n;}
  function badge(status){return el('span',labels[status]||status||T('未记录'),'badge '+tone(status));}
  function duration(value){if(!Number.isFinite(value))return T('未记录');const n=Math.max(0,Math.round(value));return n>=3600?T("{0}时{1}分" , Math.floor(n/3600), Math.floor(n%3600/60)):n>=60?T("{0}分{1}秒" , Math.floor(n/60), n%60):T("{0}秒" , n);}
  function time(value){return new Date(value*1000).toLocaleTimeString(WorkflowI18n.locale(),{hour12:false});}
  function notice(text){$('notice').textContent=text;$('notice').hidden=!text;}
  async function api(path,options={}){const epoch=state.epoch,controller=new AbortController(),timer=setTimeout(()=>controller.abort(),12000);try{const response=await fetch(path,{...options,headers:{...(state.token?{Authorization:'Bearer '+state.token}:{}),...options.headers},signal:controller.signal,cache:'no-store'});if(response.status===401){const message=localViewer?T('连接已失效，请让 AI 重新打开会话面板。'):T('令牌无效或已失效，请重新连接。');if(epoch===state.epoch){disconnect();notice(message);}throw new Error(message);}const data=await response.json();if(!response.ok)throw new Error(T(data.error)||T('读取失败'));return data;}finally{clearTimeout(timer);}}
  function showError(e){state.connectionError=true;notice(e.name==='AbortError'?T('读取超时，请检查连接后刷新。'):e.message||T('连接失败，请重试。'));$('connection').textContent=state.connected?T('连接异常'):T('未连接');}
  function disconnect(){motionVisibility.disconnect();state.demo=null;demoControls(false);graphRun='';inspectorKey='';for(const n of [...transitions.keys()])stopTransition(n);state.epoch++;state.detailRequest++;state.logRequest++;Object.assign(state,{token:'',connected:false,connectionError:false,detail:null,log:null,runs:[],panel:'',task:''});state.known.clear();$('token').value='';$('login').hidden=localViewer;$('session-login').hidden=!localViewer;$('main').hidden=true;$('connection').textContent=T('未连接');document.title=T('工作流 · 运行看板');for(const id of ['nodes','edges','detail-content','task-info','log','timeline','messages','run-select'])$(id).replaceChildren();}
  function setHash(){if(state.demo!==null)return;const p=new URLSearchParams({run:state.run});if(state.panel==='task'&&state.task)p.set('task',state.task);history.replaceState(null,'','#'+p);}
  function notifyChanges(runs){let needs=0;for(const r of runs){const status=r.service_status==='recovery_needed'?r.service_status:r.status,n=['failed','blocked','awaiting_review'].reduce((sum,k)=>sum+(r.counts?.[k]||0),0);if(n||['error','warn'].includes(tone(status)))needs++;const old=state.known.get(r.id);state.known.set(r.id,{status,n});if(old&&(n>old.n||status!==old.status&&['good','error','warn'].includes(tone(status)))&&state.notifications&&window.Notification?.permission==='granted'){try{new Notification(T('工作流状态变化'),{body:`${workflowRunName(r)}：${labels[status]||status}`,tag:r.id});}catch(_){}}}document.title=(needs?`(${needs}) `:'')+T('工作流 · 运行看板');}
  function renderRunPicker(){const select=$('run-select'),signature=JSON.stringify(state.runs.map(r=>[r.id,workflowRunName(r)]));if(select.dataset.signature!==signature){select.replaceChildren(...state.runs.map(r=>{const o=el('option',workflowRunName(r));o.value=r.id;return o;}));select.dataset.signature=signature;}select.value=state.run;}
  async function refresh(){if(!state.connected||state.busy||state.demo!==null)return;state.busy=true;$('refresh').disabled=true;const epoch=state.epoch;try{const result=await api('/api/observe');if(epoch!==state.epoch)return;state.runs=result.runs;state.readAt=result.read_at;state.listTruncated=result.truncated;notifyChanges(state.runs);if(!state.runs.some(r=>r.id===state.run)){state.run=state.runs[0]?.id||'';state.task='';state.panel='';state.artifact='';state.detail=null;state.log=null;}renderRunPicker();$('list-note').textContent=result.truncated?T('只显示最近 100 个运行'):'';$('empty').hidden=!!state.run;$('run-detail').hidden=!state.detail;if(state.run)await loadSelected();if(epoch!==state.epoch)return;state.connectionError=false;$('connection').textContent=T('已连接 · 只读');$('freshness').textContent=time(result.read_at)+T(' 更新');notice('');}catch(e){if(epoch===state.epoch)showError(e);}finally{state.busy=false;$('refresh').disabled=state.demo!==null;}}
  async function loadSelected(){const run=state.run,epoch=state.epoch,request=++state.detailRequest;if(!run)return;const data=await api('/api/observe/'+run);if(run!==state.run||epoch!==state.epoch||request!==state.detailRequest||state.demo!==null)return;state.detail=data;if(state.task&&!data.tasks.some(t=>t.id===state.task)){state.task='';state.panel='';}renderOverview();renderGraph();renderInspector();setHash();if(state.panel==='task'&&$('evidence').open)await loadLog();}
  function renderOverview(){const d=state.detail,c=workflowCounts(d.tasks),s=d.snapshot;$('run-detail').hidden=false;$('empty').hidden=true;const goal=WorkflowI18n.content(d,'goal');$('goal').textContent=goal;$('goal').title=goal;$('goal-more').hidden=goal.length<48;$('run-title').textContent=workflowRunName(d);$('run-status').replaceChildren(badge(d.service_status==='recovery_needed'?d.service_status:s.phase));$('progress-label').textContent=`${c.done} / ${c.total} ${d.source==='interactive'?T('已完成'):T('已集成')}`;$('progress').max=Math.max(1,c.total);$('progress').value=c.done;$('progress').setAttribute('aria-label',d.source==='interactive'?T('任务完成进度'):T('任务集成进度'));$('budget').textContent=d.demo?T("模拟步骤 {0} / 6" , d.demo_step+1):d.source==='interactive'?T('会话回报 · ')+time(d.reported_at):(s.phase==='completed'?T('用时 ')+duration(s.budget.elapsed_seconds):T('剩余 ')+duration(s.budget.remaining_seconds));$('count-all').textContent=c.total;$('count-running').textContent=c.running;$('count-attention').textContent=c.attention;const questions=s.human_requests||[];$('attention').hidden=!c.attention&&!questions.length&&d.service_status!=='recovery_needed';$('attention-label').textContent=questions.length?T("{0} 项需要你确认" , questions.length):c.attention?T("{0} 个任务需要处理" , c.attention):T('运行需要恢复');
    const latest=d.plan_changes?.at(-1);$('plan-change').textContent=T("计划 r{0}" , s.revision)+(latest?T(" · 新增 {0} · 调整依赖 {1}" , latest.added.length, latest.dependencies.length):T(' · 初始拆解'));
    const phase=d.demo?[T('并行执行'),T('发现依赖，调整计划'),T('补充前置约定'),T('恢复接口开发'),T('核对验收结果'),T('演示完成')][d.demo_step]:d.planning?.pending?T('等待新计划生效'):d.planning?.active?T('总控正在分析反馈'):s.phase==='completed'?T('执行已完成'):T('随执行自动更新');if($('plan-phase').textContent!==phase)$('plan-phase').textContent=phase;}

  function pathFor(a,b,vertical){if(vertical){const x1=a.x+a.w/2,y1=a.y+a.h,x2=b.x+b.w/2,y2=b.y;return `M${x1},${y1} C${x1},${(y1+y2)/2} ${x2},${(y1+y2)/2} ${x2},${y2}`;}const x1=a.x+a.w,y1=a.y+a.h/2,x2=b.x,y2=b.y+b.h/2;return `M${x1},${y1} C${(x1+x2)/2},${y1} ${(x1+x2)/2},${y2} ${x2},${y2}`;}
  function renderGraph(){
    if(!state.detail)return;
    const d=state.detail,vertical=matchMedia('(max-width:600px)').matches,layout=workflowGraph(d.tasks,vertical),viewport=$('graph-viewport');
    const context=state.run+(d.demo?':demo':''),same=graphRun===context;
    const active=document.activeElement,focusKey=$('nodes').contains(active)?active.dataset.node:null,scroll={left:viewport.scrollLeft,top:viewport.scrollTop};
    if(!same){motionVisibility.disconnect();for(const n of [...transitions.keys()])stopTransition(n);$('nodes').replaceChildren();$('edges').replaceChildren();graphRun=context;}
    const existing=new Map([...$('nodes').children].map(n=>[n.dataset.node,n])),oldRects=new Map([...existing].map(([id,n])=>[id,n.getBoundingClientRect()]));
    for(const b of existing.values())stopTransition(b);
    const tasks=new Map(d.tasks.map(t=>[t.id,t])),positions=new Map(layout.nodes.map(n=>[n.id,n]));
    const width=Math.max(200,viewport.clientWidth-(vertical?16:40)),scale=vertical?1:Math.min(1,Math.max(.9,width/layout.width));
    $('graph').style.width=layout.width+'px';$('graph').style.height=layout.height+'px';$('graph').style.transform=`scale(${scale})`;
    $('graph-frame').style.width=layout.width*scale+'px';$('graph-frame').style.height=layout.height*scale+'px';
    const edges=$('edges');edges.setAttribute('viewBox',`0 0 ${layout.width} ${layout.height}`);edges.setAttribute('width',layout.width);edges.setAttribute('height',layout.height);
    if(!edges.querySelector('defs')){const defs=svg('defs',{});for(const [name,color] of Object.entries({idle:'#b8c4d3',good:'#74b89e',active:'#245ac5',error:'#cf7a86'})){const marker=svg('marker',{id:'arrow-'+name,markerWidth:7,markerHeight:7,refX:6,refY:3.5,orient:'auto'});marker.append(svg('path',{d:'M0 0L7 3.5L0 7Z',fill:color}));defs.append(marker);}edges.append(defs);}
    const previousEdges=new Map([...edges.querySelectorAll('g[data-key]')].map(g=>[g.dataset.key,g]));
    for(const e of layout.edges){
      const id=JSON.stringify([e.from,e.to,e.kind]),key=tone(tasks.get(e.from)?.status),color=['good','active','error'].includes(key)?key:'idle';
      let group=previousEdges.get(id);previousEdges.delete(id);
      if(!group){group=svg('g',{'data-key':id});group.append(svg('path',{}),svg('path',{class:'edge-flow',pathLength:100}),svg('title',{}));edges.append(group);motionVisibility.observe(group);}
      const [line,flow,title]=group.children,path=pathFor(positions.get(e.from),positions.get(e.to),vertical);
      const changed=d.plan_changes?.at(-1)?.dependencies.some(c=>c.task===e.to&&c.after.includes(e.from)&&!c.before.includes(e.from));
      line.setAttribute('d',path);line.setAttribute('class','edge '+color+(e.kind==='milestone'?' milestone':'')+(changed?' revised':''));line.setAttribute('marker-end',`url(#arrow-${color})`);
      line.dataset.from=e.from;line.dataset.to=e.to;flow.setAttribute('d',path);group.classList.toggle('flowing',workflowEdgeFlow(e,d.tasks,d.snapshot.phase));
      title.textContent=(e.kind==='milestone'?T('里程碑依赖'):T('任务依赖'))+(changed?T(' · 本次计划新增'):'');
    }
    for(const group of previousEdges.values()){motionVisibility.unobserve(group);group.remove();}
    const moves=[];let cursor=$('nodes').firstElementChild;
    for(const n of layout.nodes){
      const t=tasks.get(n.id),status=t?.status||(n.kind==='delivery'?(d.snapshot.phase==='completed'?'completed':(d.snapshot.human_requests||[]).length?'awaiting_review':'pending'):'');
      let b=existing.get(n.id);existing.delete(n.id);const fresh=!b;
      if(fresh){b=el('button');b.dataset.node=n.id;const visual=el('span','','node-icon'),label=el('span','','node-label');label.append(el('strong'),el('span','','node-activity'),el('small'));b.append(visual,label,el('span','','node-revision'));$('nodes').append(b);motionVisibility.observe(b);}
      b.className='flow-node '+tone(status)+(n.kind==='goal'?' anchor':'');b.dataset.status=status;
      b.dataset.running=String(workflowNodeMotion(status,d.snapshot.phase)||n.kind==='goal'&&workflowNodeMotion('running',d.snapshot.phase)&&!!d.planning?.active);
      if(t)b.dataset.task=t.id;else delete b.dataset.task;
      b.style.left=n.x+'px';b.style.top=n.y+'px';b.style.width=n.w+'px';b.style.height=n.h+'px';
      const title=t?workflowTitle(t,d.tasks):n.kind==='goal'?T('总目标'):T('交付验收'),caption=t?labels[status]||status:n.kind==='goal'?(d.demo?T('模拟总控'):d.source==='interactive'?T('当前会话'):T('总控协调')):labels[status]||status;
      const activity=t?workflowActivity(t,d.tasks):n.kind==='goal'?(d.planning?.pending?T('等待计划生效'):d.planning?.active?T('分析执行反馈'):T("协调计划 r{0}" , d.snapshot.revision)):d.snapshot.phase==='completed'?(d.demo?T('模拟结果已就绪'):d.source==='interactive'?T('查看验证依据'):T('分支与报告已就绪')):T('等待各项结果');
      b.setAttribute('aria-label',title+T('，')+activity+T('，')+caption);b.title=title+T('：')+activity;
      const [visual,label,revision]=b.children,iconKey=t?tone(status):n.kind==='goal'?'goal':status==='completed'?'good':'delivery';
      if(visual.dataset.icon!==iconKey){visual.replaceChildren(icon(iconKey));visual.dataset.icon=iconKey;if(!fresh)transition(visual,[{transform:'scale(.78)',opacity:.5},{transform:'scale(1)',opacity:1}],280);}
      const [heading,action,small]=label.children;heading.textContent=title;
      if(action.textContent!==activity){action.textContent=activity;if(!fresh)transition(action,[{opacity:.45},{opacity:1}],180);}
      small.textContent=caption+(t?.attempts>1?T(" · 第 {0} 次" , t.attempts):'');revision.hidden=!t?.introduced_revision;revision.textContent=t?.introduced_revision?T("新增 r{0}" , t.introduced_revision):'';
      b.onclick=()=>t?selectTask(t.id):openRun();moves.push({b,n,fresh});
      if(b!==cursor){if($('nodes').moveBefore)$('nodes').moveBefore(b,cursor);else $('nodes').insertBefore(b,cursor);}cursor=b.nextElementSibling;
    }
    for(const b of existing.values()){motionVisibility.unobserve(b);stopTransition(b);for(const child of b.querySelectorAll('*'))stopTransition(child);b.remove();}
    applyHighlight();viewport.scrollLeft=scroll.left;viewport.scrollTop=scroll.top;
    // Measure in one batch, then FLIP: DOM identity, focus and loop phase survive polling.
    const newRects=new Map(moves.map(({b,n})=>[n.id,b.getBoundingClientRect()]));
    for(const {b,n,fresh} of moves){const old=oldRects.get(n.id),next=newRects.get(n.id);if(fresh&&same)transition(b,[{opacity:0,transform:'translateY(10px)'},{opacity:b.classList.contains('dimmed')?.26:1,transform:'translateY(0)'}],320);else if(old){const dx=(old.left-next.left)/scale,dy=(old.top-next.top)/scale;if(Math.abs(dx)>.5||Math.abs(dy)>.5)transition(b,[{transform:`translate(${dx}px,${dy}px)`},{transform:'translate(0,0)'}],380);}}
    $('graph-warning').hidden=!layout.warnings.length;$('graph-warning').textContent=layout.warnings.join(T('；'));
    if(focusKey&&document.activeElement!==active){const target=[...$('nodes').children].find(n=>n.dataset.node===focusKey)||$('task-search');target.focus({preventScroll:true});}
  }
  function demoControls(open){$('motion-demo-bar').hidden=!open;$('motion-demo').textContent=open?T('返回运行'):T('动效演示');$('motion-demo').setAttribute('aria-pressed',String(open));$('run-select').disabled=open;$('refresh').disabled=open;$('export').disabled=open;$('share').hidden=open;}
  function advanceDemo(){if(state.demo===null)return;state.demo.step=(state.demo.step+1)%6;demoAt=Date.now();state.detail=workflowMotionDemo(state.demo.step);if(state.task&&!state.detail.tasks.some(t=>t.id===state.task)){state.task='';state.panel='';}renderOverview();renderGraph();renderInspector();}
  $('motion-demo').onclick=()=>{
    if(state.demo===null){if(!state.detail)return;state.detailRequest++;state.logRequest++;state.demo={step:-1,detail:state.detail,task:state.task,panel:state.panel,filter:state.filter,query:$('task-search').value,artifact:state.artifact,log:state.log,technical:$('technical').open,evidence:$('evidence').open,scroll:{left:$('graph-viewport').scrollLeft,top:$('graph-viewport').scrollTop}};state.task='';state.panel='';state.filter='all';state.log=null;$('task-search').value='';$('technical').open=false;$('evidence').open=false;demoControls(true);advanceDemo();$('graph-viewport').scrollLeft=0;$('graph-viewport').scrollTop=0;}
    else {const saved=state.demo;state.demo=null;state.detail=saved.detail;state.task=saved.task;state.panel=saved.panel;state.filter=saved.filter;state.artifact=saved.artifact;state.log=saved.log;$('task-search').value=saved.query;demoControls(false);renderOverview();renderGraph();renderInspector();$('technical').open=saved.technical;$('evidence').open=saved.evidence;$('graph-viewport').scrollLeft=saved.scroll.left;$('graph-viewport').scrollTop=saved.scroll.top;setHash();refresh();}
  };
  $('demo-next').onclick=advanceDemo;
  setInterval(()=>{if(state.demo!==null&&motionAllowed()&&Date.now()-demoAt>=5000)advanceDemo();},500);
  function applyHighlight(){if(!state.detail)return;const query=$('task-search').value.trim().toLowerCase();let matched=0;for(const b of $('nodes').children){const t=state.detail.tasks.find(t=>t.id===b.dataset.task);if(!t)continue;const match=[t.id,workflowTitle(t,state.detail.tasks),t.goal,WorkflowI18n.content(t,'goal'),t.branch||'',t.module||'',workflowActivity(t,state.detail.tasks)].join(' ').toLowerCase().includes(query)&&(state.filter==='all'||state.filter==='running'&&t.status==='running'||state.filter==='attention'&&attention(t));if(match)matched++;b.classList.toggle('dimmed',!match);b.classList.toggle('selected',state.panel==='task'&&state.task===t.id);b.setAttribute('aria-pressed',String(state.panel==='task'&&state.task===t.id));}for(const button of document.querySelectorAll('[data-filter]'))button.setAttribute('aria-pressed',String(button.dataset.filter===state.filter));$('search-note').hidden=!query||matched>0;$('search-note').textContent=T('没有匹配节点');}
  function openRun(){state.panel='run';renderInspector();setHash();$('more').open=false;focusInspector();}
  function selectTask(id){const changed=state.task!==id;state.task=id;state.panel='task';if(changed){state.artifact='';state.log=null;$('evidence').open=false;$('technical').open=false;}renderInspector();applyHighlight();setHash();focusInspector();if($('evidence').open)loadLog().catch(showError);}
  function focusInspector(){if(matchMedia('(max-width:800px)').matches)$('inspector').scrollIntoView({block:'start'});$('detail-title').focus({preventScroll:true});}
  function closeInspector(){const id=state.task;state.panel='';state.task='';$('evidence').open=false;renderInspector();applyHighlight();setHash();const node=[...$('nodes').children].find(n=>n.dataset.node===id)||$('about');node.focus({preventScroll:true});}
  function appendGoal(target,owner){const raw=owner.goal,text=WorkflowI18n.content(owner,'goal');target.append(el('p',text,'detail-goal'));if(text!==raw){const original=el('details','','source-text');original.append(el('summary',T('查看原文')),el('p',raw,'detail-goal'));target.append(original);}}
  function renderInspector(){const d=state.detail,open=!!state.panel,key=open?state.panel+':'+state.task:'';$('inspector').hidden=!open;if(key&&key!==inspectorKey)transition($('inspector'),[{opacity:.65,transform:'translateY(8px)'},{opacity:1,transform:'translateY(0)'}],220);inspectorKey=key;$('workspace').classList.toggle('with-detail',open);if(!open||!d)return;const content=$('detail-content');content.replaceChildren();$('task-tools').hidden=state.panel!=='task';if(state.panel==='run'){$('detail-title').textContent=T('运行信息');content.append(badge(d.snapshot.phase));appendGoal(content,d);for(const q of d.snapshot.human_requests||[])content.append(el('p',q.question||q.text||q.summary||JSON.stringify(q),'question'));const facts=el('dl');for(const [k,v] of [[T('运行'),workflowRunName(d)],[T('下次额度重试'),d.snapshot.quota_wait?new Date(d.snapshot.quota_wait.retry_at*1000).toLocaleString():T('无')],[T('计划修订'),d.snapshot.revision],[T('已用时间'),duration(d.snapshot.budget.elapsed_seconds)],[T('剩余时间'),duration(d.snapshot.budget.remaining_seconds)],[T('查看范围'),d.source==='interactive'?T('由当前会话记录进度；本页不运行代码，完成状态需结合验证依据。'):T('只读；刷新不会派工。仅显示服务中登记的运行。')]])facts.append(el('dt',k),el('dd',String(v)));content.append(facts);if(d.planning?.pending)content.append(el('p',T('总控已提出修订，等待活动任务结束后校验生效。'),'next-step'));for(const change of [...(d.plan_changes||[])].reverse()){const section=el('section','','revision-entry');section.append(el('h3',T("计划 r{0}" , change.revision)),el('p',WorkflowI18n.content(change,'reason')));if(change.added.length)section.append(el('p',T('新增任务：')+change.added.join(T('、'))));for(const dep of change.dependencies)section.append(el('p',T("{0} 的前置任务：{1} → {2}" , dep.task, dep.before.join(T('、'))||T('无'), dep.after.join(T('、'))||T('无'))));content.append(section);}return;}
    const t=d.tasks.find(t=>t.id===state.task);if(!t)return;$('detail-title').textContent=workflowTitle(t,d.tasks);content.append(badge(t.status));appendGoal(content,t);content.append(el('p',workflowActivity(t,d.tasks),'current-action'));if(t.activity)content.append(el('p',T("{0} · 第 {1} 次 · {2}{3}" , d.source==='interactive'?T('会话回报'):T('工作者回报'), t.activity.attempt, time(t.activity.at), t.activity.truncated?T(' · 摘要已截断'):''),'small muted'));if(t.support_for)content.append(el('p',T(t.support_kind==='feature'?"r{0} 新增 · 从 {1} 拆出的功能":"r{0} 新增 · 为 {1} 补充准备工作" , t.introduced_revision, t.support_for),'next-step'));if(t.completion_kind==='validated_children')content.append(el('p',T('子功能已交付，原任务完整验收通过'),'check-summary'));if(t.reason)content.append(el('p',t.reason==='task verification failed'?T('验收检查未通过'):WorkflowI18n.content(t,'reason'),'problem'));if(attention(t))content.append(el('p',T(t.next_step),'next-step'));const failed=t.checks.filter(c=>c.result!=='passed').length;content.append(el('p',d.source==='interactive'?(t.evidence?.length?T("已记录 {0} 项验证依据" , t.evidence.length):T('尚无验证依据')):t.checks.length?T("{0} 项检查通过{1}" , t.checks.length-failed, failed?' · '+failed+T(' 项未通过'):''):T('尚无验收结果'),'check-summary'));$('show-log').textContent=failed?T('查看错误日志'):T('查看运行日志');$('show-log').hidden=d.source==='interactive';$('evidence').hidden=d.source==='interactive';$('technical').querySelector('summary').textContent=d.source==='interactive'?T('验证依据'):T('分支与执行记录');
    const info=$('task-info');info.replaceChildren();const facts=el('dl');for(const [k,v] of [[T('任务编号'),t.id],[T('分支'),t.branch||T('尚未创建')],[T('提交'),t.commit||T('尚未提交')],[T('任务依赖'),t.depends_on.join(T('、'))||T('无')],[T('里程碑依赖'),(t.requires||[]).join(T('、'))||T('无')],[T('尝试'),String(t.attempts)]])facts.append(el('dt',k),el('dd',v,k===T('分支')||k===T('提交')?'code':''));if(d.source==='interactive'){for(const item of t.evidence||[])info.append(el('p',item,'attempt'));if(!t.evidence?.length)info.append(el('p',T('尚未记录验证依据'),'small muted'));}else info.append(facts);for(const p of t.processes){const a=el('div','','attempt');a.append(el('strong',T("尝试 {0} · {1}" , p.attempt, p.reason==='exited'&&p.exit_code===0?T('进程正常退出'):p.environment_failure||p.reason||T('未记录'))),el('p',`${duration(p.duration_seconds)} · exit ${p.exit_code??T('未记录')}`));info.append(a);}for(const c of t.checks){const row=el('p','','attempt');row.append(badge(c.result),el('span',' '+c.check_id));info.append(row);}if(t.failure_cases.length)info.append(el('p',T('失败记录：')+t.failure_cases.map(c=>c.id).join(T('、')),'small muted'));
    const phases={published:T('已发布'),tested:T('已测试'),merged:T('已合并'),accepted:T('回归通过'),reverted:T('已回退')};
    for(const pr of t.feature_prs||[]){const row=el('p','','attempt');try{const url=new URL(pr.url);if(url.protocol==='https:'&&url.hostname==='github.com'&&/^\/[^/]+\/[^/]+\/pull\/\d+$/.test(url.pathname)){const a=el('a',T('功能 PR')+' #'+pr.number);a.href=url.href;a.target='_blank';a.rel='noopener noreferrer';row.append(a);}}catch{}row.append(el('span',' · '+(phases[pr.phase]||pr.phase)));info.append(row);}
    if(d.snapshot.remote_hold)content.append(el('p',T('远程阻塞：')+d.snapshot.remote_hold,'problem'));
    const options=d.artifacts.filter(a=>!a.task_id||a.task_id===t.id);if(!options.some(a=>a.id===state.artifact))state.artifact=(options.find(a=>a.kind==='text'&&a.path.startsWith('checks/task-')&&a.task_id===t.id&&a.attempt===t.attempts)||options.find(a=>a.kind==='text'&&a.attempt===t.attempts&&a.bytes>0)||options.at(-1))?.id||'';const signature=JSON.stringify(options.map(a=>[a.id,WorkflowI18n.content(a,'label')]));if($('artifact').dataset.signature!==signature){$('artifact').replaceChildren(...options.map(a=>{const o=el('option',WorkflowI18n.content(a,'label'));o.value=a.id;return o;}));$('artifact').dataset.signature=signature;}$('artifact').value=state.artifact;$('artifact').disabled=!options.length;
    $('timeline').replaceChildren();for(const e of d.timeline.filter(e=>!e.data.task||e.data.task===t.id)){const li=el('li');li.append(el('time',time(e.at)),el('strong',T(e.kind)),el('p',Object.entries(e.data).map(([k,v])=>`${k}: ${typeof v==='string'?v:JSON.stringify(v)}`).join(' · ')));$('timeline').append(li);}$('timeline-note').textContent=t.id+T(' 与全局事件')+(d.timeline_truncated?T(' · 最近 200 条'):'');$('messages').replaceChildren();for(const m of d.messages.filter(m=>m.sender===t.id||m.recipient===t.id)){const row=el('div','','message');row.append(el('strong',`${m.sender} → ${m.recipient}`),el('span',`${m.kind} · ${m.acked?T('已确认'):T('未确认')} · ${time(m.at)}`));$('messages').append(row);}if(!$('messages').children.length)$('messages').append(el('p',T('暂无消息记录'),'muted small'));
  }
  async function loadLog(){const request=++state.logRequest,run=state.run,artifact=state.artifact,epoch=state.epoch;if(!artifact){state.log=null;$('log').textContent=T('暂无日志');$('log-meta').textContent='';return;}if(!state.log||state.log.artifact.id!==artifact){$('log').textContent=T('正在读取…');$('log-meta').textContent='';}const data=await api(`/api/observe/${run}/logs/${artifact}`);if(request!==state.logRequest||run!==state.run||artifact!==state.artifact||epoch!==state.epoch)return;state.log=data;renderLog();}
  function renderLog(){const data=state.log;if(!data)return;const query=$('log-search').value.toLowerCase(),found=data.text.split('\n').filter(line=>!query||line.toLowerCase().includes(query));$('log').textContent=found.join('\n')||(query?T('当前窗口没有匹配行'):T('日志暂为空'));$('log-meta').textContent=data.artifact.path+(data.truncated?T(' · 已截断'):'')+(query?T(" · {0} 行匹配" , found.length):'')+(state.detail?.artifacts_truncated?T(' · 证据目录达到上限'):'');if($('follow').checked)$('log').scrollTop=$('log').scrollHeight;}
  $('login-form').onsubmit=async e=>{e.preventDefault();state.token=$('token').value.trim();if(!state.token)return;state.connected=true;state.epoch++;$('token').value='';$('login').hidden=true;$('main').hidden=false;const p=new URLSearchParams(location.hash.slice(1));state.run=p.get('run')||'';state.task=p.get('task')||'';state.panel=state.task?'task':'';await refresh();if(!state.runs.length&&!state.detail&&state.connectionError){disconnect();notice(T('无法连接，请检查令牌和网络。'));}};
  $('run-select').onchange=()=>{state.run=$('run-select').value;state.task='';state.panel='';state.artifact='';state.detail=null;state.log=null;state.logRequest++;$('evidence').open=false;$('run-detail').hidden=true;setHash();loadSelected().catch(showError);};
  $('logout').onclick=async()=>{try{if(localViewer)await api('/api/session/disconnect',{method:'POST'});disconnect();notice(T('已断开连接'));}catch(e){showError(e);}};$('refresh').onclick=refresh;$('about').onclick=openRun;$('plan-change').onclick=openRun;$('goal-more').onclick=openRun;$('close-detail').onclick=closeInspector;$('task-search').oninput=applyHighlight;
  $('attention').onclick=()=>{if(state.detail.snapshot.human_requests?.length||state.detail.service_status==='recovery_needed')openRun();else{const task=state.detail.tasks.find(attention);if(task)selectTask(task.id);}};
  for(const b of document.querySelectorAll('[data-filter]'))b.onclick=()=>{state.filter=b.dataset.filter;applyHighlight();};
  $('show-log').onclick=()=>{$('evidence').open=true;$('tab-log').click();loadLog().catch(showError);$('log').focus({preventScroll:true});$('evidence').scrollIntoView({block:'nearest'});};$('evidence').ontoggle=()=>{if($('evidence').open&&state.panel==='task')loadLog().catch(showError);};$('artifact').onchange=()=>{state.artifact=$('artifact').value;state.log=null;loadLog().catch(showError);};$('log-search').oninput=renderLog;
  for(const name of ['log','timeline','messages']){const tab=$('tab-'+name);tab.onclick=()=>{for(const n of ['log','timeline','messages']){$('panel-'+n).hidden=n!==name;$('tab-'+n).setAttribute('aria-selected',String(n===name));$('tab-'+n).tabIndex=n===name?0:-1;}};tab.onkeydown=e=>{const names=['log','timeline','messages'];if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const index=e.key==='Home'?0:e.key==='End'?2:(names.indexOf(name)+(e.key==='ArrowRight'?1:2))%3,next=$('tab-'+names[index]);next.click();next.focus();}};}$('tab-log').click();
  $('share').onclick=async()=>{setHash();try{await navigator.clipboard.writeText(location.href);notice(localViewer?T('已复制定位链接，同一浏览器可继续查看。'):T('已复制定位链接，访问仍需令牌。'));}catch(_){notice(T('请复制地址栏中的定位链接。'));}};
  $('export').onclick=()=>{if(!state.detail)return;const payload={scope:T('当前运行的诊断摘要与已打开的日志窗口，不是完整日志备份。分享前检查内容。'),exported_at:new Date().toISOString(),detail:state.detail,selected_log:state.log},url=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:'application/json'})),a=el('a');a.href=url;a.download=`workflow-${state.detail.snapshot.run_id}-diagnostic.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);$('more').open=false;};
  $('notify').onclick=async()=>{if(!('Notification' in window)){notice(T('当前浏览器不支持通知。'));return;}if(state.notifications){state.notifications=false;$('notify').textContent=T('开启浏览器提醒');return;}const permission=await Notification.requestPermission();state.notifications=permission==='granted';$('notify').textContent=state.notifications?T('关闭浏览器提醒'):T('开启浏览器提醒');notice(state.notifications?T('页面打开期间提醒后续状态变化。'):T('通知未获允许，仍可在图中查看状态。'));};
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&state.panel)closeInspector();});document.addEventListener('click',e=>{if(!$('more').contains(e.target))$('more').open=false;});
  window.addEventListener('hashchange',()=>{if(new URLSearchParams(location.hash.slice(1)).has('connect')){connectSession();return;}if(!state.connected||state.demo!==null)return;const p=new URLSearchParams(location.hash.slice(1));state.run=p.get('run')||state.run;state.task=p.get('task')||'';state.panel=state.task?'task':'';state.artifact='';loadSelected().catch(showError);});
  let width=0;new ResizeObserver(entries=>{const next=Math.round(entries[0].contentRect.width);if(next&&next!==width){width=next;renderGraph();}}).observe($('graph-viewport'));
  async function connectSession(){
    if(!localViewer||sessionConnecting)return;
    sessionConnecting=true;$('reconnect').disabled=true;
    const p=new URLSearchParams(location.hash.slice(1)),code=p.get('connect');
    if(code){p.delete('connect');history.replaceState(null,'',location.pathname+(p.size?'#'+p:''));}
    $('login').hidden=true;$('session-login').hidden=true;$('connection').textContent=T('正在连接…');
    try{
      let data=await api('/api/session/status');
      if(!data.connected&&code)data=await api('/api/session/connect',{method:'POST',headers:{'X-Workflow-Connect':code}});
      if(!data.connected){disconnect();notice(T('请让 AI 打开本次任务的流程图，无需填写访问令牌。'));return;}
      state.token='';state.connected=true;state.epoch++;
      state.run=p.get('run')||'';state.task=p.get('task')||'';state.panel=state.task?'task':'';
      $('session-login').hidden=true;$('main').hidden=false;await refresh();
    }catch(e){disconnect();showError(e);}finally{sessionConnecting=false;$('reconnect').disabled=false;}
  }
  WorkflowI18n.apply(document);
  $('language').value=WorkflowI18n.locale();
  $('language').onchange=()=>{
    WorkflowI18n.setLocale($('language').value,true);WorkflowI18n.apply(document);
    if(state.demo!==null)state.detail=workflowMotionDemo(state.demo.step);
    renderRunPicker();
    if(state.detail){renderOverview();renderGraph();renderInspector();}
    if(state.log)renderLog();syncMotion();demoControls(state.demo!==null);
    if(state.readAt)$('freshness').textContent=time(state.readAt)+T(' 更新');
    $('list-note').textContent=state.listTruncated?T('只显示最近 100 个运行'):'';
    $('notify').textContent=state.notifications?T('关闭浏览器提醒'):T('开启浏览器提醒');
    $('connection').textContent=state.connectionError?T('连接异常'):state.connected?T('已连接 · 只读'):T('未连接');
    // Prior notices may contain server-authored diagnostics; retain them verbatim.
    if(state.connected)notifyChanges(state.runs);
  };
  $('reconnect').onclick=connectSession;connectSession();
  setInterval(()=>{if(!document.hidden&&$('auto').checked&&state.connected)refresh();},3000);document.addEventListener('visibilitychange',()=>{syncMotion();if(!document.hidden&&state.connected&&$('auto').checked)refresh();});
})();
