// Lightweight execution of the real workspace without jsdom.
import fs from 'node:fs';
import vm from 'node:vm';

let pass=0,fail=0;
function check(cond,label){if(cond){pass++;console.log('PASS',label);}else{fail++;console.log('FAIL',label);}}

class ClassList{
  constructor(){this.values=new Set();}
  add(...xs){xs.forEach(x=>this.values.add(x));}
  remove(...xs){xs.forEach(x=>this.values.delete(x));}
  contains(x){return this.values.has(x);}
  toggle(x,on){if(on===undefined)on=!this.contains(x);on?this.add(x):this.remove(x);return on;}
}
class El{
  constructor(tag='div'){
    this.tagName=tag.toUpperCase();this.children=[];this.textContent='';this.value='';
    this.hidden=false;this.disabled=false;this.className='';this.classList=new ClassList();
    this.attrs={};this.listeners={};this.focused=false;this._innerHTML='';
  }
  appendChild(x){this.children.push(x);return x;}
  addEventListener(type,fn){this.listeners[type]=fn;}
  setAttribute(k,v){this.attrs[k]=String(v);}
  getAttribute(k){return this.attrs[k];}
  focus(){this.focused=true;}
  click(){if(this.listeners.click)return this.listeners.click({currentTarget:this});}
  scrollIntoView(){}
  set innerHTML(v){this._innerHTML=v;this.children=[];}
  get innerHTML(){return this._innerHTML;}
}
function allText(n){return [n.textContent||'',...(n.children||[]).map(allText)].join(' ');}

const ids=[
  'workspace','app-shell','main','workspace-current','workspace-current-title',
  'workspace-current-pit','workspace-current-detail','workspace-current-continue','workspace-recent',
  'workspace-recovery','workspace-recovery-count','workspace-photo-count',
  'workspace-photo-summary','workspace-photo-action','workspace-find',
  'saved-pits-search','loc','site','campaign','date','pitid','s11'
];
const els=Object.fromEntries(ids.map(id=>[id,new El(id==='saved-pits-search'?'input':'div')]));
els.workspace.hidden=false;els['app-shell'].hidden=true;els['workspace-current'].hidden=true;
els['workspace-photo-action'].hidden=true;els['saved-pits-search'].value='Upper Ridge';
els.site.value='Upper Ridge';els.campaign.value='WY2026';els.date.value='2026-01-20';
els.pitid.textContent='UPR20260120';
const body=new El('body');
let loaded=null,recovered=null,newPitCalls=0,indexForce=null,navForce=null;
const response={
  recent:[
    {site_id:'s1',pit_id:'ALPHA',site:'Upper Ridge',campaign:'WY2026',date:'2026-01-10',pending_photos:1,missing_attachments:0},
    {site_id:'s2',pit_id:'BRAVO',site:'Lower Basin',campaign:'WY2026',date:'2026-01-11',pending_photos:0,missing_attachments:1},
  ],
  total_pits:2,
  recovery:[{site_id:'r1',pit_id:'RECOVER',updated_at:'2026-03-02 08:00:00'}],
  recovery_count:1,expected_photos:2,missing_attachments:1,
};
const context={
  console,setTimeout,clearTimeout,Promise,API:'',ENABLE_EDIT:true,
  _loaded_site_id:null,_loaded_pit_id:null,
  document:{
    body,getElementById:id=>els[id]||null,
    createElement:tag=>new El(tag),
    querySelector:sel=>sel==='[data-t="s11"]'?els.s11:null,
  },
  globalThis:null,
  matchMedia:()=>({matches:false}),
  gv:id=>els[id]?.value||'',
  formDirty:()=>false,
  setst(){},
  toggleIndex:v=>{indexForce=v;},toggleNav:v=>{navForce=v;},
  loadPit:(id,pid)=>{loaded={id,pid};},
  recoverPit:(id,pid)=>{recovered={id,pid};return Promise.resolve({ok:true});},
  newPit:async()=>{newPitCalls++;},
  attachmentOutboxAllSummary:async()=>({total:3,contexts:2,available:true,persistence:'persistent'}),
  fetch:async url=>({ok:url==='/api/workspace',status:200,json:async()=>response}),
};
context.globalThis=context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(new URL('../cryopit/static/js/65_workspace.js',import.meta.url),'utf8'),context,{filename:'65_workspace.js'});

context.initWorkspace();
await new Promise(resolve=>setTimeout(resolve,5));
check(body.classList.contains('workspace-open'),'workspace mode is marked on the body');
check(els.workspace.hidden===false&&els['app-shell'].hidden===true,'workspace is the initial view and the form shell is hidden');
check(els['workspace-recent'].children.length===2,'recent owner-scoped pits render in the workspace');
check(allText(els['workspace-recent']).includes('1 photo pending')&&allText(els['workspace-recent']).includes('1 missing'),'recent records surface attachment states');
check(els['workspace-recovery-count'].textContent==='2'&&allText(els['workspace-recovery']).includes('RECOVER'),'recovery and storage-integrity work are visible separately');
check(allText(els['workspace-recovery']).includes('Attachment integrity'),'missing stored files appear as workspace attention items');
await new Promise(resolve=>setTimeout(resolve,5));
check(els['workspace-photo-count'].textContent==='3','workspace counts all browser-local queued photographs');
check(els['workspace-photo-summary'].textContent.includes('2 pit or draft contexts'),'photo summary reports queue isolation by context');
check(els['workspace-photo-summary'].textContent.includes('server expects 2'),'photo summary distinguishes server expectations');

// Finder transition keeps the existing filter DOM untouched.
context.openSavedPitsFinder();
check(els.workspace.hidden===true&&els['app-shell'].hidden===false,'Edit Existing Pit opens the field shell');
check(els['saved-pits-search'].focused,'finder search receives focus');
check(els['saved-pits-search'].value==='Upper Ridge','moving between workspace and finder preserves filters');
check(indexForce===false,'finder expands the section index without clearing its state');

// Loaded/current-record awareness.
context._loaded_site_id='s-current';context._loaded_pit_id='UPR20260120';
context.refreshWorkspaceCurrent();
check(els['workspace-current'].hidden===false,'loaded record appears in current-work card');
check(els['workspace-current-title'].textContent==='Currently editing','current-work card distinguishes archived edit mode');
check(els['workspace-current-pit'].textContent==='UPR20260120','current-work card shows the visible pit identity');
check(els['workspace-current-detail'].textContent.includes('Upper Ridge')&&els['workspace-current-detail'].textContent.includes('WY2026'),'current-work card shows field context');
check(els['workspace-current-continue'].textContent==='Continue record'&&els['workspace-current-continue'].attrs['aria-label']==='Continue record','loaded record uses a readable Continue record action');

context._loaded_site_id=null;context._loaded_pit_id=null;context.formDirty=()=>true;
context.refreshWorkspaceCurrent();
check(els['workspace-current-title'].textContent==='Current draft','current-work card distinguishes an unarchived draft');
check(els['workspace-current-continue'].textContent==='Continue draft'&&els['workspace-current-continue'].attrs['aria-label']==='Continue draft','unarchived work uses Continue draft');

// Recent and recovery controls carry immutable identities.
const firstRecent=els['workspace-recent'].children[0];firstRecent.click();
check(loaded?.id==='s1'&&loaded?.pid==='ALPHA','recent pit loads by immutable site_id');
const firstRecovery=els['workspace-recovery'].children[0];firstRecovery.click();
check(recovered?.id==='r1','workspace recovery action carries immutable site_id');

// A genuinely clean new record opens directly; dirty work uses the established safeguard.
context._loaded_site_id=null;context.formDirty=()=>false;
await context.workspaceStartNewPit();
check(els['app-shell'].hidden===false&&els.loc.focused,'clean Start New Pit opens and focuses the form without a discard prompt');
context.formDirty=()=>true;
await context.workspaceStartNewPit();
check(newPitCalls===1,'dirty Start New Pit delegates to the existing queue-and-draft safeguard');

if(fail){console.error(`${fail} workspace UI tests failed`);process.exit(1);}
console.log(`${pass} workspace UI tests passed`);
