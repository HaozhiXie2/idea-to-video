/* 灵感影坊 — no remote frontend dependencies, no generated demo results. */
(function (root) {
  'use strict';
  const DRAFT_KEY = 'idea-to-video:idea-draft:v1';
  const TEMPLATES = {
    story: {name:'故事剧情',symbol:'◒',hint:'让人物和故事动起来',placeholder:'你想讲一个怎样的故事？可以只写一句想法，也可以粘贴完整章节。\n\n还没想好的人物、风格和结局，我们会先问你，不会擅自补上。'},
    product: {name:'产品展示',symbol:'◇',hint:'把产品的特点讲清楚',placeholder:'介绍一下你的产品、真实特点和展示目的。\n\n比如：一只米白色陶瓷杯，想拍出安静的手作质感。没有提供的性能和卖点不会替你编造。'},
    knowledge: {name:'知识示意',symbol:'✳',hint:'让抽象知识变直观',placeholder:'你想解释哪个知识点？给谁看？\n\n可以粘贴你已经确认的知识内容。我们会先确认表达方式，不把模型猜测当作事实。'},
    memory: {name:'生活纪念',symbol:'⌁',hint:'把珍贵时刻留下来',placeholder:'说说你想记录的时刻、人物和情绪。\n\n有哪些一定要保留的真实细节？不确定的形象和情节，我们会等你确认。'}
  };
  const STATUS = {queued:'等待本机提交',new:'等待本机提交',submitting:'正在提交',waiting:'平台处理中',downloading:'下载结果中',done:'已完成',failed:'生成失败',uncertain:'提交结果不明',canceled:'已取消待提交',cancelled:'已取消待提交',rendering:'本机合成中',working:'处理中'};
  const PHASE = {idea:'等待完善方案',questions:'有问题待回答',plan:'方案待确认',assets:'确定关键形象',storyboard:'检查分镜草稿',video:'制作动态镜头',complete:'已有成片'};
  const ROLE = {face:'面部特写',full_body:'全身与服装',product:'产品参考',scene:'场景参考',style:'风格参考'};
  const KIND = {asset_image:'关键形象图片',shot_image:'分镜图片',shot_video:'动态镜头'};
  const ACTIVE = new Set(['queued','new','submitting','waiting','downloading']);
  function safeMediaUrl(value) {
    if (typeof value !== 'string' || !value.startsWith('/media/') || /[\\\x00-\x20]/.test(value)) return '';
    try { const decoded = decodeURIComponent(value); if (decoded.includes('..') || decoded.includes('\\') || /[\x00-\x20]/.test(decoded)) return ''; } catch (_) { return ''; }
    return value;
  }
  function versionsOf(entity,kind) { return (entity?.versions || []).filter(v=>v.kind===kind && safeMediaUrl(v.url)); }
  function selectedVersion(entity,kind) { return versionsOf(entity,kind).find(v=>v.id===entity?.['selected_'+kind]) || null; }
  function readyImage(shot) { return Boolean(selectedVersion(shot,'image') && !shot.image_stale); }
  function readyVideo(shot) { return Boolean(readyImage(shot) && selectedVersion(shot,'video') && !shot.video_stale); }
  function trialValid(p) { const s=(p.shots||[]).find(s=>s.id===p.trial?.shot_id);return Boolean(p.trial?.approved&&s&&readyVideo(s)&&s.selected_video===p.trial.version_id); }
  function pendingIds(project,kind) {
    const jobs=project.jobs||[], candidates=kind==='asset_image'?project.assets:project.shots;
    return (candidates||[]).filter(e=>!(jobs.some(j=>j.entity_id===e.id && j.kind===kind && (ACTIVE.has(j.status)||j.status==='uncertain'))))
      .filter(e=>kind==='shot_video'?!readyVideo(e):kind==='shot_image'?!readyImage(e):!e.approved).map(e=>e.id);
  }
  function gate(project,action) {
    const p=project||{}, a=p.approvals||{}, shots=p.shots||[], assets=p.assets||[];
    if(action==='assets') return !!a.plan;
    if(action==='storyboard') return !!a.assets;
    if(action==='approve-assets') return ['assets','storyboard'].includes(p.planning_stage) && assets.every(e=>e.approved && selectedVersion(e,'image'));
    if(action==='approve-storyboard') return !!a.assets && shots.length>0 && shots.every(readyImage);
    if(action==='motion') return !!a.storyboard && !!p.trial?.shot_id && readyVideo(shots.find(s=>s.id===p.trial.shot_id)||{});
    if(action==='video-batch') return !!a.storyboard && trialValid(p);
    if(action==='final') return !!a.storyboard && trialValid(p) && shots.length>0 && shots.every(readyVideo);
    if(action==='draft') return shots.length>0 && shots.every(readyImage);
    return false;
  }
  function questionStage(project) { return project.questions_stage || project.planning_stage || 'plan'; }
  function workspaceFor(project) {
    if (!project?.approvals?.plan) return 'plan';
    if (!project?.approvals?.assets) return 'assets';
    return 'film';
  }
  function summarizeChanges(changes) {
    const labels={description:'画面描述',camera:'镜头运动',action:'主体动作',image_prompt:'生图提示词',video_prompt:'视频提示词',prompt:'形象提示词',name:'名称'};
    return Object.entries(changes||{}).map(([k,v])=>(labels[k]||k)+'：'+(typeof v==='string'?v:JSON.stringify(v,null,2))).join('\n\n');
  }
  function publicDraft(data) {
    return {source:String(data.source||'').slice(0,60000),title:String(data.title||'').slice(0,120),template:Object.hasOwn(TEMPLATES,data.template)?data.template:'story',duration:[15,30,60,90].includes(Number(data.duration))?Number(data.duration):30,ratio:['9:16','16:9','1:1'].includes(data.ratio)?data.ratio:'9:16'};
  }
  const exports={DRAFT_KEY,TEMPLATES,STATUS,safeMediaUrl,versionsOf,selectedVersion,readyImage,readyVideo,trialValid,pendingIds,gate,questionStage,workspaceFor,summarizeChanges,publicDraft};
  if(typeof module!=='undefined'&&module.exports) module.exports=exports;
  if(typeof document==='undefined') return;
  const $=id=>document.getElementById(id);
  const state={project:null,projects:[],status:null,workspace:'plan',busy:false,polling:false};
  function h(tag,attrs,...children) {
    const node=document.createElement(tag);
    for(const [name,value] of Object.entries(attrs||{})) {
      if(value===null||value===undefined||value===false) continue;
      if(name==='class') node.className=value;
      else if(name.startsWith('on')&&typeof value==='function') node.addEventListener(name.slice(2).toLowerCase(),value);
      else if(name==='checked'||name==='disabled'||name==='hidden'||name==='selected'||name==='controls'||name==='muted') node[name]=Boolean(value);
      else if(name==='value') node.value=value;
      else node.setAttribute(name,String(value));
    }
    for(const child of children.flat(Infinity)) if(child!==null&&child!==undefined&&child!==false) node.append(child instanceof Node?child:document.createTextNode(String(child)));
    return node;
  }
  const btn=(label,action,style='secondary',disabled=false)=>h('button',{type:'button',class:'button '+style,onclick:action,disabled},label);
  const tag=(label,tone='')=>h('span',{class:'tag '+tone},label);
  const row=(...children)=>h('div',{class:'button-row'},...children);
  const field=(label,node,hint)=>h('div',{class:'field'},h('label',{for:node.id||undefined},label),node,hint?h('small',{},hint):null);
  const notice=(text,tone='info',action)=>h('div',{class:'notice '+tone},h('p',{},text),action||null);
  const details=(label,...body)=>h('details',{class:'disclosure'},h('summary',{},label),...body);
  const detailBox=(label,value)=>h('div',{class:'detail-box'},h('small',{},label),h('p',{},value||'未提供'));
  const stamp=(n,label)=>h('div',{class:'hero-stamp','aria-hidden':'true'},h('span',{},'YOUR IDEA'),h('b',{},n),h('span',{},label));
  function toast(message,error=false) { const t=$('toast');t.textContent=message;t.className='toast'+(error?' error':'');t.hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>{t.hidden=true;},error?11000:5500); }
  async function api(path,body) {
    let response;
    try { response=await fetch(path,{method:body===undefined?'GET':'POST',cache:'no-store',headers:body===undefined?{}:{'Content-Type':'application/json','X-Studio-Token':document.querySelector('meta[name="studio-token"]').content},body:body===undefined?undefined:JSON.stringify(body)}); }
    catch (_) { throw new Error('没有收到本机服务的回复。请检查启动器；如果刚提交了生成，请先刷新任务记录，不要重复创建任务。'); }
    let data;try{data=await response.json();}catch(_){throw new Error('服务返回了无法读取的内容，请检查本机运行日志。');}
    if(!response.ok) throw new Error(data.error||'操作没有完成，请稍后查看真实状态。');
    return data;
  }
  const endpoint=(suffix,id=state.project?.id)=>'/api/projects/'+encodeURIComponent(id)+'/'+suffix;
  async function guarded(button,work) {
    if(button?.disabled) return;
    const label=button?.textContent;if(button){button.disabled=true;button.replaceChildren(h('span',{class:'spinner','aria-hidden':'true'}),'处理中…');}
    try{return await work();}catch(error){toast(error.message,true);}finally{if(button?.isConnected){button.disabled=false;button.textContent=label;}}
  }
  function applyProject(p,workspace) { state.project=p;if(workspace)state.workspace=workspace;upsert(p);render(); }
  function upsert(p) { const i=state.projects.findIndex(x=>x.id===p.id);if(i<0)state.projects.unshift(p);else state.projects[i]=p;renderProjects(); }
  async function mutate(suffix,body={},workspace) { const p=state.project;const result=await api(endpoint(suffix),{expected_revision:p.revision,...body});applyProject(result,workspace);return result; }
  async function openProject(id) { await guarded(null,async()=>{const p=await api('/api/projects/'+encodeURIComponent(id));state.workspace=workspaceFor(p);applyProject(p);}); }
  function renderProjects() {
    $('project-count').textContent=String(state.projects.length);
    $('project-list').replaceChildren(...state.projects.map(p=>h('button',{class:'project-item'+(p.id===state.project?.id?' active':''),onclick:()=>openProject(p.id),'aria-current':p.id===state.project?.id?'page':null},h('strong',{},p.title||'未命名作品'),h('small',{},(TEMPLATES[p.template]?.name||'作品')+' · '+p.duration+' 秒 · '+(PHASE[p.phase]||'草稿')))));
    if(!state.projects.length)$('project-list').append(h('p',{class:'muted small'},'还没有作品，从一个想法开始。'));
  }
  function statusDisplay() {
    const e=$('connection-status');
    e.className='status-pill '+(state.status?'good':'warn');e.textContent=state.status?'本机已连接':'本机未连接';
    e.title=state.status?'这个页面只连接当前电脑的独立工作台。':'请打开灵感影坊启动器。';
  }
  function serviceNotice() {
    if(state.status?.test_mode)return notice('离线验收环境：内容和生成结果为测试桩，不调用云端、不扣费，也不代表 AI 生成质量。','');
    if(!state.status)return notice('暂时连接不到本机服务。请确认独立启动器正在运行，刷新后继续。','error',btn('重新连接',()=>boot(),'outline compact'));
    if(!state.status.text_configured)return notice('开始分析前，还差一次文字服务设置。未配置时不会生成演示方案，也不会扣取生成额度。','',btn('设置文字服务',()=>openSettings(),'outline compact'));
    return null;
  }
  function loadDraft() {try{return publicDraft(JSON.parse(localStorage.getItem(DRAFT_KEY)||'{}'));}catch(_){return publicDraft({});}}
  function saveDraft(draft){try{localStorage.setItem(DRAFT_KEY,JSON.stringify(publicDraft(draft)));}catch(_){/* Private mode may disable storage; the form still works. */}}
  function render() {
    $('history-button').hidden=!state.project;
    $('workspace').replaceChildren(...(state.project?renderProject():renderCreate()));
  }
  function renderCreate() {
    const draft=loadDraft();
    const source=h('textarea',{id:'idea-source',class:'source-input',maxlength:60000,placeholder:TEMPLATES[draft.template].placeholder,value:draft.source,'aria-label':'你的想法或正文'});
    const title=h('input',{id:'idea-title',maxlength:120,value:draft.title,placeholder:'可选，例如：雨后的重逢'});
    const duration=h('select',{id:'idea-duration'},...[15,30,60,90].map(v=>h('option',{value:v,selected:v===draft.duration},v+' 秒')));
    const ratio=h('select',{id:'idea-ratio'},...Object.entries({'9:16':'竖屏 · 9:16','16:9':'横屏 · 16:9','1:1':'方形 · 1:1'}).map(([v,l])=>h('option',{value:v,selected:v===draft.ratio},l)));
    const count=h('span',{},draft.source.length.toLocaleString()+' / 60,000 字');
    const save=()=>{draft.source=source.value;draft.title=title.value;draft.duration=Number(duration.value);draft.ratio=ratio.value;saveDraft(draft);count.textContent=source.value.length.toLocaleString()+' / 60,000 字';};
    [source,title,duration,ratio].forEach(e=>e.addEventListener('input',save));
    const templates=h('div',{class:'templates','aria-label':'选择创作类型'});
    for(const [key,t] of Object.entries(TEMPLATES))templates.append(h('button',{type:'button',class:'template-card','aria-pressed':key===draft.template?'true':'false',onclick:()=>{draft.template=key;source.placeholder=t.placeholder;templates.querySelectorAll('button').forEach(e=>e.setAttribute('aria-pressed','false'));templates.children[Object.keys(TEMPLATES).indexOf(key)].setAttribute('aria-pressed','true');save();}},h('span',{class:'template-symbol','aria-hidden':'true'},t.symbol),h('b',{},t.name),h('small',{},t.hint)));
    const file=h('input',{type:'file',accept:'.txt,.md,.markdown,text/plain,text/markdown',hidden:true,'aria-label':'导入 TXT 或 Markdown'});
    file.addEventListener('change',async()=>{const f=file.files?.[0];if(!f)return;try{if(f.size>2*1024*1024)throw new Error('文本文件过大，请使用不超过 60,000 字的内容。');const text=await f.text();if(text.length>60000)throw new Error('正文超过 60,000 字，请分成多个作品制作。');source.value=text;save();toast('文本已导入，只保存在本机草稿。');}catch(e){toast(e.message,true);}file.value='';});
    const submit=btn('保存想法，开始创作  →',e=>guarded(e.currentTarget,async()=>{save();if(!source.value.trim())throw new Error('先写下一句话或导入正文吧。');const p=await api('/api/projects',publicDraft(draft));try{localStorage.removeItem(DRAFT_KEY);}catch(_){}applyProject(p,'plan');toast('想法已保存在本机，下一步完善制作方案。');}),'dark');
    return [
      h('section',{class:'hero'},h('div',{},h('span',{class:'eyebrow'},'A LITTLE IDEA, A NEW STORY'),h('h1',{},'你来想象，',h('em',{},'我们一起成片。')),h('p',{},'一句想法，一段文字，或一个值得留下的瞬间。先把方向聊清楚，再一步步做出你想要的画面。')),stamp('01','从想法开始')),
      h('div',{class:'onboarding-strip'},...[[1,'定方案','把内容、风格和边界说清楚'],[2,'定形象','角色、产品与场景，先看再确认'],[3,'做成片','先看分镜，再试一个动态镜头']].map(([n,t,s])=>h('div',{class:'mini-step'},h('span',{class:'number'},'0'+n),h('div',{},h('strong',{},t),h('p',{},s))))),
      serviceNotice(),
      h('div',{class:'create-grid'},h('section',{class:'panel'},h('div',{class:'panel-header'},h('div',{},h('div',{class:'section-kicker'},'THE STARTING POINT'),h('h2',{},'这次，你想做点什么？')),tag('新作品')),templates,h('div',{class:'field'},h('div',{class:'field-header'},h('label',{for:'idea-source'},'说说你的想法'),btn('导入 TXT / MD',()=>file.click(),'subtle compact')),source,file),h('div',{class:'source-footer'},h('span',{},'不必写专业提示词，用自己的话就好。'),count),h('div',{class:'form-row three'},field('作品名称',title),field('计划时长',duration),field('画面比例',ratio)),h('div',{class:'form-actions'},h('p',{},'现在只保存你的想法，不调用云端服务。\n首版输出无声 MP4。'),submit)),h('aside',{class:'side-note'},h('div',{class:'note-icon','aria-hidden':'true'},'✳'),h('h3',{},'把选择权，',h('br'), '留在你手里。'),h('p',{},'缺少的信息会集中问你。不是让 AI 猜你的想法，而是陪你把想法说清楚。'),h('hr'),...['先确认方案与关键形象，再生成镜头。','每次付费生成，都先显示任务数量。','哪里不满意就改哪里，旧版本仍保留。'].map(t=>h('div',{class:'note-row'},h('span',{},'↗'),h('p',{},t)))) )
    ].filter(Boolean);
  }
  function renderProject() {
    const p=state.project,a=p.approvals||{};
    const tabs=[['plan','定方案','你的故事与表达方向',a.plan],['assets','定形象','先看关键参考图',a.assets],['film','做成片','分镜、试镜与导出',gate(p,'final')&&(p.exports||[]).some(e=>e.kind==='final'&&e.status==='done'&&!e.stale)]];
    return [h('div',{class:'workspace-head'},h('div',{},h('span',{class:'eyebrow'},'YOUR DIRECTOR’S DESK'),h('h1',{},p.title||'未命名作品'),h('div',{class:'workspace-meta'},tag(TEMPLATES[p.template]?.name||'创作'),tag(p.duration+' 秒'),tag(p.ratio),tag(PHASE[p.phase]||'创作中','good'))),row(btn('创作设置',()=>openProjectSettings(),'outline compact'),btn('服务设置',()=>openSettings(),'outline compact'))),
      h('nav',{class:'step-nav','aria-label':'创作工作区'},...tabs.map(([id,name,sub,done],i)=>h('button',{class:'step-tab'+(state.workspace===id?' active':'')+(done?' done':''),'aria-current':state.workspace===id?'step':null,onclick:()=>{state.workspace=id;render();}},h('span',{class:'step-num'},done?'✓':'0'+(i+1)),h('span',{},h('strong',{},name),h('small',{},sub))))),
      p.text_error?notice(p.text_error,'error'):null,
      p.text_status==='analyzing'?notice('文字服务正在处理你的内容。这里只显示实际处理状态，不预测完成时间。'):null,
      state.workspace==='plan'?renderPlan():state.workspace==='assets'?renderAssets():renderFilm(),
      renderJobs()
    ].filter(Boolean);
  }
  function empty(title,text,action){return h('div',{class:'empty-state'},h('div',{class:'empty-symbol','aria-hidden':'true'},'✳'),h('h3',{},title),h('p',{},text),action||null);}
  function analyzeButton(stage,label,answers) {
    return btn(label,()=>analysisConsent(stage,answers),'dark',state.project.text_status==='analyzing');
  }
  function analysisConsent(stage,answers={}) {
    if(!state.status?.text_configured){openSettings();return;}
    const labels={plan:'完善制作方案',assets:'整理关键形象',storyboard:'编写详细分镜'};
    confirmDialog(labels[stage],['将把本作品的输入内容、已确认信息和本轮回答发给你配置的文字服务。','这会调用一次文字接口，可能产生文字费用；不会生成图片或视频，也不会自动重试失败请求。'],async()=>{
      state.busy=true;
      try{const p=await api(endpoint('analyze'),{expected_revision:state.project.revision,stage,answers});applyProject(p,stage==='storyboard'?'film':stage);toast(p.questions?.length?'有些信息需要你确认，回答后继续。':'这一阶段已整理好，请检查。');}
      finally{state.busy=false;}
    },'同意并开始分析');
  }
  function questionsBlock(stage) {
    const p=state.project;if(!p.questions?.length||questionStage(p)!==stage)return null;
    const answers={};
    const cards=p.questions.map((q,i)=>{
      const input=h('textarea',{id:'answer-'+i,placeholder:'用自己的话补充，也可以先写“还没想好”。',maxlength:6000,value:p.answers?.[q.id]||''});answers[q.id]=input.value;
      input.addEventListener('input',()=>{answers[q.id]=input.value;});
      const options=row(...(q.options||[]).map(o=>h('button',{class:'choice',type:'button',onclick:e=>{input.value=typeof o==='string'?o:String(o.label||o.value||'');answers[q.id]=input.value;e.currentTarget.parentNode.querySelectorAll('button').forEach(b=>b.classList.remove('selected'));e.currentTarget.classList.add('selected');}},typeof o==='string'?o:String(o.label||o.value||''))));
      return h('div',{class:'question-card'},h('label',{for:input.id},String(i+1).padStart(2,'0')+'  '+q.question),options,input);
    });
    return h('section',{class:'panel'},h('div',{class:'panel-header'},h('div',{},h('div',{class:'section-kicker'},'LET’S MAKE IT YOURS'),h('h2',{},'先对齐这几个选择'),h('p',{},'不知道可以说不知道。相关内容会先停住，不会替你做创作决定。')),tag(p.questions.length+' 个问题','warn')),...cards,analyzeButton(stage,'带着我的回答继续  →',answers));
  }
  function approval(stage,title,description,enabled,next) {
    const approved=stage==='motion'?trialValid(state.project):state.project.approvals?.[stage];
    return h('div',{class:'approval-bar'},h('div',{},h('strong',{},title),h('p',{},description)),approved?h('span',{class:'approved-mark'},'✓ 已确认'):btn('我确认，继续  →',e=>guarded(e.currentTarget,()=>mutate('approve',{stage},next)),'',!enabled));
  }
  function renderPlan() {
    const p=state.project,plan=p.plan,questions=questionsBlock('plan');
    const content=[serviceNotice(),questions];
    if(plan)content.push(h('section',{class:'panel'},h('div',{class:'panel-header'},h('div',{},h('div',{class:'section-kicker'},'THE CREATIVE BRIEF'),h('h2',{},'这是你的制作方案')),tag('确认点 01','good')),h('p',{class:'plan-summary'},plan.summary),h('div',{class:'detail-grid'},detailBox('视觉风格',plan.style),detailBox('给谁看',plan.audience)),...(plan.beats||[]).map((beat,i)=>h('div',{class:'detail-box',style:'margin-top:12px'},h('small',{},'段落 '+(i+1)+' · '+beat.title),h('p',{},beat.content),beat.source_excerpt?details('对应原文',h('p',{},beat.source_excerpt)):null)),plan.creative_notes?.length?details('创作边界与注意事项',h('ul',{class:'bullet-list'},...plan.creative_notes.map(n=>h('li',{},n)))):null,details('查看我的原始输入',h('p',{},p.source)),!p.approvals.plan&&!questions?row(analyzeButton('plan','重新整理方案')):null),approval('plan','先确认“做什么”和“怎么表达”','关键形象会在下一步单独确认；方案确认不触发图片或视频生成。',!p.questions?.length,'assets'));
    else if(!questions)content.push(empty('先把方向聊清楚','我们会根据你的输入，先确认用途、受众、风格与内容边界。形象和分镜会在后面的阶段分别讨论。',analyzeButton('plan','开始整理制作方案  →')),details('已保存的输入内容',h('p',{},p.source)));
    return h('div',{class:'section-stack'},...content.filter(Boolean));
  }
  function renderAssets() {
    const p=state.project;if(!gate(p,'assets'))return empty('先确认制作方案','方案确认之后，再讨论真正需要的角色、产品和场景。并非每条视频都需要一个主角。',btn('回到制作方案',()=>{state.workspace='plan';render();}));
    const questions=questionsBlock('assets'),planned=['assets','storyboard'].includes(p.planning_stage);
    const parts=[h('div',{class:'section-title'},h('div',{},h('h2',{},'先把重要的样子定下来'),h('p',{},'一张一张看，一张一张选。你选中的参考图，才会进入后续生成。')),tag('确认点 02','good')),notice('角色面部与全身服装分别确认；参考图能降低偏差，但不能保证每一帧绝对一致。生成或引用你的素材时，它们会发送给即梦。'),questions];
    if(!planned&&!questions)parts.push(empty('哪些形象值得先确认？','根据已确认方案整理角色、产品、场景。不确定的样貌和细节会先问你。',analyzeButton('assets','整理关键形象  →')));
    if(p.assets?.length)parts.push(h('div',{class:'asset-grid'},...p.assets.map(renderAsset)));
    else if(planned&&!questions)parts.push(empty('这份方案不需要单独锁定形象','不用为了走流程而多生成一张图。确认后，可以直接进入分镜。'));
    if(planned)parts.push(approval('assets','关键形象是否符合你的想法？','每个必需形象都要明确选中一个版本；风格参考是可选项，不额外强制付费。',gate(p,'approve-assets')&&!questions,'film'));
    return h('div',{class:'section-stack'},...parts.filter(Boolean));
  }
  function renderAsset(asset) {
    const chosen=selectedVersion(asset,'image');const latest=versionsOf(asset,'image').at(-1);const shown=chosen||latest;
    const image=h('div',{class:'asset-media'},shown?h('img',{src:safeMediaUrl(shown.url),alt:asset.name+'参考图',loading:'lazy'}):h('div',{class:'empty-art'},h('b',{'aria-hidden':'true'},asset.type==='character'?'◒':asset.type==='product'?'◇':'⌁'),h('span',{},'尚未生成或导入参考图')),tag(ROLE[asset.reference_role]||'关键参考',asset.approved?'good':''));
    const upload=h('input',{type:'file',accept:'image/png,image/jpeg,image/webp',hidden:true,'aria-label':'为'+asset.name+'导入参考图'});
    upload.addEventListener('change',()=>uploadAsset(asset,upload));
    const actions=row(btn(shown?'再生成 1 张':'生成 1 张候选',()=>quote('asset_image',[asset.id]),'secondary compact'),btn('导入自己的图',()=>upload.click(),'outline compact'),shown&&!asset.approved?btn('用这张图',e=>guarded(e.currentTarget,()=>mutate('select',{entity:'asset',entity_id:asset.id,kind:'image',version_id:shown.id})),'compact'):null,asset.approved?tag('✓ 已确认','good'):null);
    const style=h('input',{type:'checkbox','aria-label':'将'+asset.name+'用作全片风格参考',checked:state.project.style_asset_id===asset.id,disabled:!asset.approved});
    style.addEventListener('change',()=>guarded(null,async()=>{await mutate('style-reference',{asset_id:style.checked?asset.id:null});toast('风格参考已更新。只影响依赖它的后续画面。');}));
    return h('article',{class:'asset-card'},image,h('div',{class:'asset-body'},h('div',{class:'section-title'},h('h3',{},asset.name),tag(asset.type==='character'?'角色':asset.type==='product'?'产品':asset.type==='style'?'风格':'场景')),h('p',{},asset.description),actions,upload,h('label',{class:'consent'},style,h('span',{},'同时用作全片风格参考（可选，不另生成一张）')),row(btn('比较 / 恢复版本 ('+versionsOf(asset,'image').length+')',()=>versionsDialog('asset',asset,'image'),'subtle compact',!shown),btn('用普通话修改',()=>editDialog('asset',asset),'subtle compact'),btn('添加局部变体',()=>variantDialog(asset),'subtle compact')),details('高级：形象提示词',h('p',{},asset.prompt))));
  }
  async function uploadAsset(asset,input) {
    const file=input.files?.[0];if(!file)return;
    await guarded(null,async()=>{if(file.size>8*1024*1024)throw new Error('参考图不能超过 8 MB。');if(!/\.(png|jpe?g|webp)$/i.test(file.name))throw new Error('请使用 PNG、JPEG 或 WebP 图片。');const data=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(String(r.result).split(',')[1]);r.onerror=()=>reject(new Error('无法读取这张图片。'));r.readAsDataURL(file);});await mutate('upload',{asset_id:asset.id,data,name:file.name});toast('图片已导入本机，请检查后选择“用这张图”。');});input.value='';
  }
  function renderFilm() {
    const p=state.project;if(!gate(p,'storyboard'))return empty('先确认关键形象','先把角色、产品或场景参考图选好，分镜才能使用你认可的形象。',btn('去确定形象',()=>{state.workspace='assets';render();}));
    const questions=questionsBlock('storyboard');
    const parts=[h('div',{class:'section-title'},h('div',{},h('h2',{},'先看分镜，再让画面动起来'),h('p',{},'静态草稿看构图与节奏，动态试镜看动作与运镜。两次检查解决不同的问题。')),tag('确认点 03','good')),questions];
    if(!p.shots?.length&&!questions)parts.push(empty('把已确认的方案变成镜头','每个镜头都会有原文依据、画面描述和适配即梦的提示词。分镜写好后，不会自动生成图片。',analyzeButton('storyboard','开始编写详细分镜  →')));
    if(p.shots?.length){
      const imageIds=pendingIds(p,'shot_image'),videoIds=pendingIds(p,'shot_video'),limit=p.batch_limit||3;
      parts.push(h('div',{class:'panel flat'},h('div',{class:'section-title'},h('div',{},h('h3',{},'01 / 检查静态分镜'),h('p',{},p.shots.length+' 个镜头 · '+p.shots.reduce((n,s)=>n+s.duration,0)+' 秒 · 每批最多 '+limit+' 个任务')),row(btn('生成待补分镜图 ('+Math.min(imageIds.length,limit)+')',()=>quote('shot_image',imageIds.slice(0,limit)),'secondary compact',!imageIds.length),btn('播放静态草稿',e=>exportFilm('draft',e.currentTarget),'outline compact',!gate(p,'draft')))),h('p',{class:'muted small'},'这里只选择前 '+limit+' 个待补镜头；余下镜头需你再次确认任务数量后提交。静态草稿不是最终动态视频。')),
        h('div',{class:'shot-list'},...p.shots.map((s,i)=>renderShot(s,i))),
        approval('storyboard','画面、顺序与节奏都符合预期吗？','所有镜头必须有当前有效的分镜图。确认后先制作一个动态试镜，不直接批量消耗视频额度。',gate(p,'approve-storyboard'),'film'));
      if(p.approvals.storyboard)parts.push(renderTrial(),h('div',{class:'panel'},h('div',{class:'section-title'},h('div',{},h('h3',{},'03 / 分批完成，导出成片'),h('p',{},'已经满意的试镜直接进入成片，不必重复生成。')),tag('无声 MP4')),row(btn('生成剩余动态镜头 ('+Math.min(videoIds.length,limit)+')',()=>quote('shot_video',videoIds.slice(0,limit)),'dark',!gate(p,'video-batch')||!videoIds.length),btn('合成并导出成片',e=>exportFilm('final',e.currentTarget),'coral',!gate(p,'final'))),!p.trial?.approved?h('p',{class:'muted small',style:'margin-top:13px'},'请先观看并确认动态试镜，再开放批量视频生成。'):null));
    }
    if(p.exports?.length)parts.push(renderExports());
    return h('div',{class:'section-stack'},...parts.filter(Boolean));
  }
  function renderShot(shot,index) {
    const image=selectedVersion(shot,'image'),video=selectedVersion(shot,'video'),p=state.project;
    const trialChosen=p.trial?.shot_id===shot.id;
    const frame=h('div',{class:'shot-frame'},video&&readyVideo(shot)?h('video',{src:safeMediaUrl(video.url),controls:true,preload:'metadata','aria-label':shot.title+'动态视频'}):image?h('img',{src:safeMediaUrl(image.url),alt:shot.title+'分镜图片',loading:'lazy'}):h('span',{class:'empty-art','aria-hidden':'true'},'▧'),tag(readyVideo(shot)?'动态镜头':readyImage(shot)?'静态分镜':image?'画面已过期，需更新':'待生成分镜',readyVideo(shot)?'good':image&&shot.image_stale?'warn':''));
    const videAllowed=!!p.approvals.storyboard&&(p.trial?.approved||trialChosen);
    return h('article',{class:'shot-card'},h('div',{class:'shot-main'},frame,h('div',{class:'shot-description'},h('div',{class:'section-title'},h('h3',{},String(index+1).padStart(2,'0')+' / '+shot.title),row(tag(shot.duration+' 秒'),trialChosen?tag('试镜','good'):null)),h('p',{},shot.description),h('div',{class:'shot-tech'},detailBox('主体动作',shot.action),detailBox('镜头运动',shot.camera)),row(btn(image?'重做分镜图':'生成分镜图',()=>quote('shot_image',[shot.id]),'secondary compact'),btn(video?'重做动态镜头':'生成动态镜头',()=>quote('shot_video',[shot.id]),'outline compact',!videAllowed||!readyImage(shot)),btn('用普通话修改',()=>editDialog('shot',shot),'outline compact')),row(btn('图片版本 ('+versionsOf(shot,'image').length+')',()=>versionsDialog('shot',shot,'image'),'subtle compact',!versionsOf(shot,'image').length),btn('视频版本 ('+versionsOf(shot,'video').length+')',()=>versionsDialog('shot',shot,'video'),'subtle compact',!versionsOf(shot,'video').length),btn('移除镜头',()=>removeShot(shot),'subtle compact')))),h('div',{class:'shot-prompts'},details('镜头依据与专业提示词',shot.source_excerpt?h('div',{class:'prompt-block'},h('h4',{},'对应输入内容'),h('p',{},shot.source_excerpt)):null,h('div',{class:'prompt-block'},h('h4',{},'生图提示词'),h('p',{},shot.image_prompt)),h('div',{class:'prompt-block'},h('h4',{},'视频提示词'),h('p',{},shot.video_prompt)),h('p',{},'引用形象：'+((shot.asset_ids||[]).map(id=>p.assets.find(a=>a.id===id)?.name||id).join('、')||'无')))));
  }
  function renderTrial() {
    const p=state.project,trial=p.trial||{},chosen=p.shots.find(s=>s.id===trial.shot_id);
    const select=h('select',{id:'trial-shot'},h('option',{value:'',selected:!chosen},'选择一个正式镜头作为试镜'),...p.shots.map(s=>h('option',{value:s.id,selected:s.id===trial.shot_id},s.title+' · '+s.duration+' 秒'+(s.duration===5?'（优先试镜）':''))));
    const choose=btn('设为动态试镜',e=>guarded(e.currentTarget,async()=>{if(!select.value)throw new Error('先选择一个镜头。');await mutate('trial',{shot_id:select.value});}),'outline compact');
    return h('section',{class:'panel'},h('div',{class:'panel-header'},h('div',{},h('h3',{},'02 / 先试一个动态镜头'),h('p',{},'试镜使用正式镜头的真实时长（5 或 10 秒），满意后直接用于成片。')),tag(trial.approved?'已通过试镜':'先小批试错',trial.approved?'good':'warn')),field('选择试镜镜头',select),row(choose,chosen?btn('生成 '+chosen.duration+' 秒试镜',()=>quote('shot_video',[chosen.id]),'secondary compact'):null),chosen&&readyVideo(chosen)?h('div',{style:'margin-top:20px'},h('video',{src:safeMediaUrl(selectedVersion(chosen,'video').url),controls:true,preload:'metadata',class:'trial-video','aria-label':'动态试镜预览'}),approval('motion','动作与镜头运动是否自然？','如果只是动作不满意，用普通话修改动态指导即可，不必重做已满意的静态画面。',gate(p,'motion'),'film')):h('p',{class:'muted small',style:'margin-top:16px'},'试镜未确认前，不会开放其他镜头的视频生成。'));
  }
  function renderJobs() {
    const p=state.project;if(!p.jobs?.length)return null;
    const remaining=p.jobs.filter(j=>['queued','new'].includes(j.status)).length;
    return h('section',{class:'panel flat job-panel'},h('div',{class:'section-title'},h('div',{},h('h3',{},'任务记录'),h('p',{},'只展示真实状态。不预测平台队列位置或完成时间。')),row(tag(p.paused?'后续提交已暂停':'队列可继续',p.paused?'warn':'good'),btn(p.paused?'恢复后续提交':'暂停后续提交',e=>guarded(e.currentTarget,()=>mutate('pause',{paused:!p.paused})),'outline compact'),remaining?btn('取消 '+remaining+' 个未提交任务',()=>confirmDialog('取消还没提交的任务',['仅取消本机队列中尚未发送的任务。平台正在处理的任务不会被取消，也不承诺退款。'],()=>mutate('pause',{paused:true,cancel_queued:true}),'取消未提交任务'),'outline compact'):null)),h('div',{class:'job-list'},...[...p.jobs].reverse().map(j=>{const entity=[...(p.assets||[]),...(p.shots||[])].find(e=>e.id===j.entity_id);return h('div',{class:'job-row'},h('div',{},h('div',{class:'job-title'},h('strong',{},entity?.name||entity?.title||'生成任务'),tag(KIND[j.kind]||j.kind),tag(STATUS[j.status]||j.status,j.status==='done'?'good':['failed','uncertain'].includes(j.status)?'warn':'')),h('p',{},j.message||'等待状态更新'),j.query_error?h('p',{},'查询暂未成功：'+j.query_error):null,j.submit_id?h('div',{class:'job-id'},'平台任务 '+j.submit_id):null),row(['waiting','downloading','uncertain','failed'].includes(j.status)?btn(j.status==='uncertain'?'关联 / 查询已有任务':'只查询状态',()=>recoverDialog(j),'outline compact'):null));})));
  }
  function renderExports(){return h('section',{},h('div',{class:'section-title'},h('div',{},h('h2',{},'我的导出'),h('p',{},'静态草稿仅用于审阅；最终成片为无声视频。旧导出不会覆盖。'))),h('div',{class:'export-grid'},...[...state.project.exports].reverse().map(e=>h('article',{class:'export-card'},row(h('h3',{},e.kind==='draft'?'静态分镜草稿':'无声成片'),tag(STATUS[e.status]||e.status,e.status==='done'?'good':'')),h('p',{},e.message||(e.kind==='draft'?'由静态图片构成，不是已生成的动态视频。':'由当前确认的真实动态镜头拼接。')),e.revision!==state.project.revision?h('p',{class:'muted small'},'导出时版本 '+e.revision+'，当前作品版本 '+state.project.revision+'。'):null,safeMediaUrl(e.url)?h('video',{src:safeMediaUrl(e.url),controls:true,preload:'metadata','aria-label':e.kind==='draft'?'静态草稿视频':'最终无声成片'}):null,safeMediaUrl(e.url)?h('a',{class:'button secondary',href:safeMediaUrl(e.url),download:''},e.kind==='draft'?'下载静态草稿':'下载 MP4 成片'):null))));}
  async function exportFilm(kind,button){await guarded(button,async()=>{await api(endpoint('export'),{expected_revision:state.project.revision,kind});toast(kind==='draft'?'已开始在本机合成静态草稿。':'已开始在本机合成无声成片。');await refreshCurrent(true);});}
  function showDialog(title,subtitle,...body){const close=btn('×',()=>closeDialog(),'close-button');close.className='close-button';close.setAttribute('aria-label','关闭弹窗');$('dialog-content').replaceChildren(h('div',{class:'dialog-inner'},h('div',{class:'dialog-header'},h('div',{},h('h2',{id:'dialog-title'},title),subtitle?h('p',{},subtitle):null),close),...body));if(!$('studio-dialog').open)$('studio-dialog').showModal();}
  function closeDialog(){const password=$('studio-dialog').querySelector('input[type=password]');if(password)password.value='';$('studio-dialog').close();$('dialog-content').replaceChildren();}
  function dialogError(error){let box=$('studio-dialog').querySelector('.dialog-error');if(!box){box=h('div',{class:'dialog-error',role:'alert'});$('dialog-content').querySelector('.dialog-inner').append(box);}box.textContent=error.message||String(error);}
  function confirmDialog(title,paragraphs,work,label='确认继续'){
    const acknowledgement=h('input',{type:'checkbox',id:'dialog-consent'});
    const submit=btn(label,async e=>{const button=e.currentTarget;if(!acknowledgement.checked)return;button.disabled=true;button.textContent='处理中…';try{await work();closeDialog();}catch(error){dialogError(error);button.disabled=false;button.textContent=label;}},'dark',true);
    acknowledgement.addEventListener('change',()=>{submit.disabled=!acknowledgement.checked;});
    showDialog(title,'请先确认这次操作。',...paragraphs.map(text=>h('p',{class:'muted',style:'margin-bottom:13px'},text)),h('label',{class:'consent',for:'dialog-consent'},acknowledgement,h('span',{},'我已了解以上内容，并确认继续。')),h('div',{class:'dialog-footer'},btn('先不做',closeDialog,'outline'),submit));
  }
  async function quote(kind,entityIds){await guarded(null,async()=>{
    const projectId=state.project.id,q=await api(endpoint('quote'),{kind,entity_ids:entityIds});const requestId=crypto.randomUUID();let submitted=false;
    const acknowledgement=h('input',{type:'checkbox',id:'paid-confirm'});
    const submit=btn('确认提交 '+q.count+' 个任务',async e=>{if(submitted||!acknowledgement.checked)return;submitted=true;const button=e.currentTarget;button.disabled=true;button.textContent='提交中…';try{const r=await api(endpoint('generate',projectId),{quote_id:q.quote_id,confirmed:true,request_id:requestId});if(state.project?.id===projectId)applyProject(r.project);closeDialog();toast('已加入本机任务队列，实际状态见任务记录。');}catch(error){submitted=false;dialogError(error);button.disabled=false;button.textContent='核对并重试同一次提交';}},'dark',true);
    acknowledgement.addEventListener('change',()=>{submit.disabled=!acknowledgement.checked;});
    showDialog('这次生成，先让你看清楚',KIND[kind]+' · 即梦云端生成',h('div',{class:'quote-count'},String(q.count),h('small',{},'个真实生成任务')),h('ul',{class:'quote-items'},...q.items.map((item,i)=>h('li',{},h('strong',{},String(i+1).padStart(2,'0')+' / '+item.label),h('span',{},KIND[kind])))),notice(q.warning||'当前无法可靠预估费用。实际额度扣除由即梦平台决定。',''),h('p',{class:'muted small'},'相关提示词及选定参考图将发送给即梦。两个本机项目如果登录同一即梦账号，仍共享平台额度和并发限制。结果不明时不会自动重新提交付费请求。'),h('label',{class:'consent',for:'paid-confirm'},acknowledgement,h('span',{},'我确认生成以上 '+q.count+' 个任务，理解费用未知并同意使用平台额度。')),h('div',{class:'dialog-footer'},btn('再检查一下',closeDialog,'outline'),submit));
  });}
  function versionsDialog(entityType,entity,kind){
    const versions=versionsOf(entity,kind).slice().reverse();
    showDialog('比较与恢复 · '+(entity.name||entity.title),'选择旧版本不会删除新版本，也不会自动调用生成服务。',h('div',{class:'compare-grid'},...versions.map(v=>{const current=entity['selected_'+kind]===v.id;return h('article',{class:'version-card'+(current?' current':'')},row(tag(current?'当前选用':'保留版本',current?'good':''),h('span',{class:'small muted'},formatDate(v.created))),kind==='image'?h('img',{src:safeMediaUrl(v.url),alt:(entity.name||entity.title)+'保留版本',loading:'lazy'}):h('video',{src:safeMediaUrl(v.url),controls:true,preload:'metadata'}),h('p',{},v.prompt||v.label||'此版本没有提示词记录'),btn(current?'正在使用':'选用这个版本',async e=>{await guarded(e.currentTarget,async()=>{await mutate('select',{entity:entityType,entity_id:entity.id,kind,version_id:v.id});closeDialog();toast('版本已选用。依赖该版本的内容会按需要重新确认。');});},current?'outline full':'secondary full',current));})),h('div',{class:'dialog-footer'},btn('完成比较',closeDialog,'outline')));
  }
  function editDialog(target,entity){
    const instruction=h('textarea',{id:'edit-instruction',placeholder:target==='shot'?'比如：镜头推进慢一点，人物动作自然一点，保留现在的画面构图。':'比如：把这套衣服改成深蓝色，其他设定不变。',maxlength:4000});
    const consent=h('input',{type:'checkbox',id:'edit-consent'});
    const submit=btn('先看看会改什么',async e=>{const button=e.currentTarget;if(!instruction.value.trim()){dialogError(new Error('先写下你想修改的地方。'));return;}button.disabled=true;button.textContent='正在分析修改…';try{const proposal=await api(endpoint('edit-proposal'),{expected_revision:state.project.revision,target,target_id:entity.id,instruction:instruction.value.trim()});showProposal(proposal);}catch(error){dialogError(error);button.disabled=false;button.textContent='先看看会改什么';}},'dark',true);
    consent.addEventListener('change',()=>{submit.disabled=!consent.checked;});
    showDialog('用自己的话修改',entity.name||entity.title,field('哪里不满意？',instruction),notice(target==='shot'?'如果只是动作或运镜问题，会只更新动态指导，不误导你重做已经满意的静态画面。':'修改形象会影响引用它的镜头。先检查影响范围，再决定是否应用。'),h('label',{class:'consent',for:'edit-consent'},consent,h('span',{},'允许将这次修改及相关设定交给文字服务分析，可能产生文字费用；不生成媒体。')),h('div',{class:'dialog-footer'},btn('取消',closeDialog,'outline'),submit));
  }
  function showProposal(proposal){
    showDialog('先检查这份修改建议','应用修改只更新设定与失效标记，不会自动花费媒体生成额度。',h('p',{class:'plan-summary'},proposal.summary),h('pre',{class:'change-list'},summarizeChanges(proposal.changes)),h('p',{class:'muted small',style:'margin-top:15px'},'受影响镜头：'+((proposal.affected_shots||[]).map(id=>state.project.shots.find(s=>s.id===(typeof id==='object'?id.id:id))?.title||String(typeof id==='object'?id.title||id.id:id)).join('、')||'无')),h('div',{class:'dialog-footer'},btn('不采用',closeDialog,'outline'),btn('确认应用修改',async e=>{await guarded(e.currentTarget,async()=>{await api(endpoint('apply-edit'),{expected_revision:proposal.revision,proposal_id:proposal.proposal_id});await refreshCurrent(true);closeDialog();toast('修改已应用，旧版本仍可比较和恢复。');});},'dark')));
  }
  function variantDialog(asset){
    const name=h('input',{id:'variant-name',placeholder:'例如：雨夜披风',maxlength:120}),description=h('textarea',{id:'variant-description',placeholder:'完整描述新造型或新场景，未填写的细节不会自动补充。',maxlength:4000}),prompt=h('textarea',{id:'variant-prompt',placeholder:'明确描述新参考图的主体、外观、服装、场景与风格。',maxlength:5000});
    const chosen=new Set(),shots=(state.project.shots||[]).filter(s=>(s.asset_ids||[]).includes(asset.id));
    showDialog('新增局部形象变体','原形象与旧图会保留。只替换你勾选的镜头，不会自动生成图片。',field('新变体名称',name),field('完整形象描述',description),details('手动填写新参考图提示词',field('新变体提示词',prompt)),h('h4',{},'应用到哪些镜头'),...shots.map(s=>{const box=h('input',{type:'checkbox'});box.addEventListener('change',()=>{if(box.checked)chosen.add(s.id);else chosen.delete(s.id);});return h('label',{class:'consent'},box,h('span',{},s.title));}),!shots.length?h('p',{class:'muted small'},'还没有引用此形象的分镜，先完成分镜后再添加局部变体。'):null,h('div',{class:'dialog-footer'},btn('取消',closeDialog,'outline'),btn('保存新变体',async e=>{await guarded(e.currentTarget,async()=>{if(!name.value.trim()||!description.value.trim()||!prompt.value.trim()||!chosen.size)throw new Error('请填写名称、完整描述、提示词，并选择受影响的镜头。');await mutate('asset-variant',{asset_id:asset.id,name:name.value.trim(),description:description.value.trim(),prompt:prompt.value.trim(),shot_ids:[...chosen]},'assets');closeDialog();toast('新变体已保存，请单独生成或导入参考图。');});},'dark',!shots.length)));
  }
  function removeShot(shot){confirmDialog('移除这个镜头？',['将移除“'+shot.title+'”，作品总时长会减少 '+shot.duration+' 秒。','旧文件和版本记录保留。没有自动补拍，也不会替你填充时长。'],()=>mutate('remove-shot',{shot_id:shot.id}),'确认移除');}
  function recoverDialog(job){
    const task=h('input',{id:'remote-task',value:job.submit_id||'',placeholder:'手工核对过的即梦任务 ID（UUID）',autocomplete:'off'});
    showDialog(job.status==='uncertain'?'恢复一个已有任务':'只查询已有任务','不会提交新任务，不会自动重试生成。',job.status==='uncertain'?notice('请先到平台核实任务是否已创建。如果有，填写准确任务 ID；不要凭猜测绑定，也不要为了等待而重复生成。',''):null,field('平台任务 ID',task,job.submit_id?'已有关联的任务 ID，通常无需更改。':'仅在你已经核实对应关系后填写。'),h('div',{class:'dialog-footer'},btn('关闭',closeDialog,'outline'),btn('只查询，不重新生成',async e=>{await guarded(e.currentTarget,async()=>{await mutate('recover',{job_id:job.id,...(task.value.trim()?{submit_id:task.value.trim()}:{})});closeDialog();toast('已查询现有任务，结果见任务记录。');});},'dark')));
  }
  function formatDate(value){if(!value)return'时间未记录';const d=new Date(typeof value==='number'&&value<1e12?value*1000:value);return Number.isNaN(d.getTime())?'时间未记录':d.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});}
  function historyDialog(){const history=state.project?.history||[];showDialog('作品版本记录','恢复整个作品的设定快照。所有媒体版本与任务记录仍会保留，恢复后需要重新确认。',history.length?h('div',{class:'history-list'},...[...history].reverse().map(v=>h('div',{class:'history-item'},h('div',{},h('p',{},v.label||'保存的版本'),h('small',{},formatDate(v.created))),btn('恢复',()=>confirmDialog('恢复这个作品版本？',['将恢复“'+(v.label||'保存的版本')+'”。当前较新的媒体文件与任务记录不会删除。','恢复后需要重新确认方案、形象、分镜及试镜。此操作不生成新媒体。'],()=>mutate('restore',{version_id:v.id}),'确认恢复'),'outline compact')))):empty('还没有历史快照','当你修改方案、切换版本或继续创作，旧设定会按操作保存。'));}
  function openProjectSettings(){const p=state.project;const limit=h('input',{id:'batch-limit',type:'number',min:1,max:18,step:1,value:p.batch_limit||3});showDialog('这个作品的生成设置','先小批生成，检查满意后再继续。',field('每批最多几个任务',limit,'默认 3 个，可设置 1–18。单次关键形象候选始终只生成 1 张。'),notice('这个上限限制每次确认的批次，不代表平台同时运行的数量。即梦账号的额度与并发由平台决定。'),h('div',{class:'dialog-footer'},btn('取消',closeDialog,'outline'),btn('保存设置',async e=>{await guarded(e.currentTarget,async()=>{const n=Number(limit.value);if(!Number.isInteger(n)||n<1||n>18)throw new Error('每批任务数需要为 1–18 的整数。');await mutate('settings',{batch_limit:n});closeDialog();});},'dark')));}
  async function openSettings(){await guarded(null,async()=>{
    const config=await api('/api/config');
    const base=h('input',{id:'config-base',type:'url',value:config.base_url||'',placeholder:'https://你的服务地址/v1',autocomplete:'off',spellcheck:'false'}),model=h('input',{id:'config-model',value:config.model||'',placeholder:'服务商提供的模型名称',autocomplete:'off',spellcheck:'false'}),key=h('input',{id:'config-key',type:'password',placeholder:config.key_configured?'已保存密钥；留空保持不变':'填入文字服务的 API 密钥',autocomplete:'new-password',spellcheck:'false'}),json=h('input',{id:'config-json',type:'checkbox',checked:config.json_mode}),clear=h('input',{id:'config-clear',type:'checkbox'});
    const save=btn('仅保存设置',async e=>{const button=e.currentTarget;button.disabled=true;try{const body={base_url:base.value.trim(),model:model.value.trim(),json_mode:json.checked,clear_key:clear.checked};if(key.value)body.api_key=key.value;await api('/api/config',body);key.value='';closeDialog();state.status=await api('/api/status');statusDisplay();render();toast('设置已保存在本机；尚未调用文字或媒体生成。');}catch(error){key.value='';dialogError(error);button.disabled=false;}},'dark');
    showDialog('连接你的创作服务','文字服务负责理解想法；即梦负责图片与视频。两种服务的费用分别由各自平台决定。',h('div',{class:'section-kicker'},'01 / 文字理解服务'),field('Chat Completions 兼容服务地址',base,'使用 HTTPS 地址；本机测试服务可用 localhost。通常填写以 /v1 结尾的基础地址。'),field('模型名称',model),field('API 密钥',key,'只保存在本机私有配置，不写入浏览器草稿，也不发送到 GitHub。'),h('label',{class:'consent',for:'config-json'},json,h('span',{},'启用 JSON 模式（仅在该服务与模型明确支持时勾选）')),config.key_configured?h('label',{class:'consent',for:'config-clear'},clear,h('span',{},'清除当前已保存的密钥')):null,h('div',{class:'section-kicker',style:'margin-top:22px'},'02 / 即梦与本机合成'),notice('即梦工具：'+(state.status?.cli_available?'已找到':'尚未安装')+'；本机视频合成：'+(state.status?.ffmpeg_available?'可用':'尚未就绪')+'。即梦使用这个项目独立的登录目录，请通过项目登录脚本完成授权。'),h('p',{class:'muted small'},'文字输入会在你点击分析后发送到文字服务；提示词与引用图片会在你确认生成后发送到即梦。保存设置本身不会检查连接，也不会消耗生成额度。'),h('div',{class:'dialog-footer'},btn('先不设置',closeDialog,'outline'),save));
  });}
  async function refreshCurrent(force=false){
    if(!state.project||state.polling||state.busy)return;
    if(!force&&($('studio-dialog').open||['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName)))return;
    state.polling=true;
    try{const p=await api('/api/projects/'+encodeURIComponent(state.project.id));if(force||JSON.stringify(p)!==JSON.stringify(state.project))applyProject(p);}catch(error){if(force)throw error;}finally{state.polling=false;}
  }
  async function boot(){
    try{const [status,projects]=await Promise.all([api('/api/status'),api('/api/projects')]);if(status.app!=='idea-to-video')throw new Error('当前端口不是灵感影坊，请检查独立启动器。');state.status=status;state.projects=Array.isArray(projects)?projects:projects.projects||[];statusDisplay();renderProjects();render();}
    catch(error){state.status=null;statusDisplay();render();toast(error.message,true);}
  }
  $('new-project').addEventListener('click',()=>{state.project=null;state.workspace='plan';renderProjects();render();});
  $('settings-button').addEventListener('click',openSettings);
  $('history-button').addEventListener('click',historyDialog);
  $('studio-dialog').addEventListener('cancel',()=>{const password=$('studio-dialog').querySelector('input[type=password]');if(password)password.value='';});
  $('studio-dialog').addEventListener('click',event=>{if(event.target===$('studio-dialog')){const r=event.target.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)closeDialog();}});
  setInterval(()=>{if(!document.hidden)refreshCurrent();},8000);
  window.addEventListener('focus',()=>refreshCurrent());
  boot();
})(typeof globalThis!=='undefined'?globalThis:this);
