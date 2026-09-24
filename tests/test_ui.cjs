'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ui = require('../app/app.js');
let passed = 0;
function test(name, fn) { fn(); passed++; console.log('PASS ' + name); }
const image = {id:'img1',kind:'image',url:'/media/project1/image.png'};
const video = {id:'vid1',kind:'video',url:'/media/project1/video.mp4'};
const shot = {id:'shot1',selected_image:'img1',selected_video:'vid1',image_stale:false,video_stale:false,versions:[image,video]};
const asset = {id:'asset1',approved:true,selected_image:'img1',versions:[image]};
function project() {return {approvals:{plan:true,assets:true,storyboard:true},assets:[asset],shots:[shot],jobs:[],planning_stage:'storyboard',trial:{shot_id:'shot1',version_id:'vid1',approved:true}};}
test('only project-local media URLs are rendered',()=>{
  assert.equal(ui.safeMediaUrl('/media/p/clip.mp4'),'/media/p/clip.mp4');
  for(const url of ['https://example.com/tracker.png','javascript:alert(1)','data:image/svg+xml,<svg>', '//other/media/a.png','/media/../config.json','/media/%2e%2e/config.json','/media/a\\b','/media/a%5cb','/media/a%00b','/media/a%20b','/media/%',' /media/a'])assert.equal(ui.safeMediaUrl(url),'',url);
});
test('no old-project or secret fields enter draft storage',()=>{
  const draft=ui.publicDraft({source:'hello',template:'product',duration:60,ratio:'1:1',api_key:'secret',base_url:'https://private',model:'private'});
  assert.deepEqual(Object.keys(draft).sort(),['source','title','template','duration','ratio'].sort());
  assert.equal(draft.template,'product');assert.equal(draft.duration,60);
  assert.equal(ui.DRAFT_KEY,'idea-to-video:idea-draft:v1');
  const invalid=ui.publicDraft({template:'injected',duration:999,ratio:'<img>',source:'a'.repeat(65000)});
  assert.equal(invalid.template,'story');assert.equal(invalid.duration,30);assert.equal(invalid.ratio,'9:16');assert.equal(invalid.source.length,60000);
});
test('four distinct templates and three workspaces',()=>{
  assert.equal(Object.keys(ui.TEMPLATES).length,4);
  assert.equal(ui.workspaceFor({approvals:{}}),'plan');
  assert.equal(ui.workspaceFor({approvals:{plan:true}}),'assets');
  assert.equal(ui.workspaceFor(project()),'film');
});
test('approval gates require current media and staged planning',()=>{
  let p=project();assert.ok(ui.gate(p,'approve-assets'));assert.ok(ui.gate(p,'approve-storyboard'));assert.ok(ui.gate(p,'draft'));assert.ok(ui.gate(p,'final'));
  p={...p,shots:[{...shot,image_stale:true,video_stale:true}]};assert.equal(ui.gate(p,'draft'),false);assert.equal(ui.gate(p,'final'),false);
  p={...project(),planning_stage:'plan'};assert.equal(ui.gate(p,'approve-assets'),false);
  p={...project(),assets:[{...asset,approved:false}]};assert.equal(ui.gate(p,'approve-assets'),false);
  p={...project(),assets:[]};assert.equal(ui.gate(p,'approve-assets'),true);
});
test('motion trial must be approved before batches or final',()=>{
  const p={...project(),trial:{shot_id:'shot1',approved:false}};
  assert.ok(ui.gate(p,'motion'));assert.equal(ui.gate(p,'video-batch'),false);assert.equal(ui.gate(p,'final'),false);
  p.trial.shot_id='missing';assert.equal(ui.gate(p,'motion'),false);
  const changed=project();changed.trial.version_id='older-video';assert.equal(ui.gate(changed,'video-batch'),false);
});
test('batch selection excludes done, active, and uncertain work',()=>{
  const p=project();assert.deepEqual(ui.pendingIds(p,'shot_video'),[]);
  p.shots=[{...shot,id:'a',video_stale:true},{...shot,id:'b',video_stale:true},{...shot,id:'c',video_stale:true}];
  p.jobs=[{entity_id:'a',kind:'shot_video',status:'uncertain'},{entity_id:'b',kind:'shot_video',status:'waiting'}];
  assert.deepEqual(ui.pendingIds(p,'shot_video'),['c']);
  p.jobs=[{entity_id:'c',kind:'shot_image',status:'waiting'}];assert.deepEqual(ui.pendingIds(p,'shot_video'),['a','b','c']);
});
test('question responses retain backend stage namespacing',()=>{
  assert.equal(ui.questionStage({questions_stage:'assets',planning_stage:'plan'}),'assets');
  assert.equal(ui.questionStage({}),'plan');
});
test('untrusted changes are plain text and unknown statuses not faked',()=>{
  assert.equal(ui.summarizeChanges({camera:'<img src=x onerror=alert(1)>'}),'镜头运动：<img src=x onerror=alert(1)>');
  assert.equal(ui.STATUS.uncertain,'提交结果不明');assert.equal(ui.STATUS.waiting,'平台处理中');
  assert.equal(ui.STATUS['50%'],undefined);
});
test('browser rendering uses safe DOM APIs and no remote dependencies',()=>{
  const js=fs.readFileSync(path.join(__dirname,'../app/app.js'),'utf8');
  const html=fs.readFileSync(path.join(__dirname,'../app/index.html'),'utf8');
  assert.ok(!/innerHTML\s*=|insertAdjacentHTML|document\.write\(/.test(js));
  assert.ok(js.includes('document.createTextNode'));
  assert.ok(!/<script[^>]*src=["']https?:|<link[^>]*href=["']https?:/.test(html));
  assert.ok(html.includes('content="__TOKEN__"'));
  assert.ok(!js.includes('novel-film-studio'));assert.ok(!js.includes('localStorage.setItem(\'api'));
  assert.ok(js.includes('费用未知'));assert.ok(js.includes('cancel_queued:true'));assert.ok(js.includes("stage==='motion'"));
});
console.log('\n'+passed+' UI test groups passed (offline, no paid calls).');
