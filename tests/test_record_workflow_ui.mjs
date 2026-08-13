// Lightweight execution of the real record-mode functions without jsdom.
import fs from 'node:fs';
import vm from 'node:vm';

let pass=0, fail=0;
function check(cond,label){if(cond){pass++;console.log('PASS',label);}else{fail++;console.log('FAIL',label);}}

function el(extra={}){
  return {hidden:false,textContent:'',className:'',disabled:false,children:[],dataset:{},
    classList:{add(){},remove(){},toggle(){},contains(){return false;}},
    setAttribute(){},appendChild(x){this.children.push(x);return x;},
    addEventListener(){},querySelector(){return null;},remove(){},...extra};
}
const els={
  'record-mode':el({hidden:true}), 'record-mode-title':el(),
  'record-mode-detail':el(), 'archive-btn':el({textContent:'Archive'}),
  'post-archive':el({hidden:true}), 'tb-st':el(),
  pitid:el({textContent:'—'}), tb:el({children:[]}), sb:el({children:[]}),
};
let confirmText=''; let reloaded=false; let populated=null; let fetched=''; let discarded=0; let switched=null;
const context={
  console, setTimeout, clearTimeout, AbortController,
  API:'', ENABLE_EDIT:true, _loaded_site_id:null, _loaded_pit_id:null, _restoring:false,
  document:{
    getElementById:id=>els[id]||null,
    createElement:tag=>el({tagName:tag.toUpperCase()}),
    createTextNode:text=>({textContent:text}), body:el(),
  },
  localStorage:{setItem(){},removeItem(){},getItem(){return null;}},
  location:{reload(){reloaded=true;}},
  confirm:msg=>{confirmText=msg;return true;},
  gv:()=>'', collect:()=>({meta:{pit_id:'P'}}), populate:p=>{populated=p;},
  refreshAttachUI(){}, _pendingTotal:()=>0, clearDraft(){}, scheduleDraft(){},
  async discardAttachmentQueue(){discarded++;}, async switchAttachmentQueueContext(id){switched=id;},
  setchip(){}, setst(){}, shortPath:x=>x, flushPendingAttachments:async()=>({done:0,failed:0,rejected:[],duplicates:[]}),
  downloadZip(){}, URL:{createObjectURL(){return'x';},revokeObjectURL(){}},
  fetch:async url=>{fetched=url;return {json:async()=>({ok:true,site_id:'site-9',pit_id:'PIT-9',pit:{meta:{pit_id:'PIT-9'}}})};},
};
vm.createContext(context);
const source=fs.readFileSync(new URL('../cryopit/static/js/60_api.js',import.meta.url),'utf8');
vm.runInContext(source,context,{filename:'60_api.js'});

context.setRecordMode('site-1','PIT-1',false);
check(vm.runInContext('_loaded_site_id',context)==='site-1','edit mode binds immutable site_id');
check(els['record-mode'].hidden===false,'edit banner is visible');
check(els['record-mode-title'].textContent==='Editing archived pit','loaded pit is identified as edit mode');
check(els['archive-btn'].textContent==='Archive Changes','primary action becomes Archive Changes');
check(els['record-mode-detail'].textContent.includes('updates this existing record'),'edit banner explains update semantics');

context.setRecordMode('site-1','PIT-1',true);
check(els['record-mode-title'].textContent==='Archive needs recovery','pending pit is labeled for recovery');
check(els['archive-btn'].textContent==='Retry Archive','pending pit changes the primary action to retry');

context.setRecordMode(null,null,false);
check(vm.runInContext('_loaded_site_id',context)===null,'new mode clears archived identity');
check(els['record-mode'].hidden===true && els['archive-btn'].textContent==='Archive','new mode restores the first-archive UI');

context.setRecordMode('site-2','PIT-2',false);
await context.newPit();
check(reloaded,'Start New Pit reloads a clean form after confirmation');
check(confirmText.includes('does not use saved pits as templates'),'Start New Pit explicitly rejects templating');
check(confirmText.includes('archived pit will remain saved'),'Start New Pit explains the existing record is retained');

// Loading is by immutable site_id, then binds the returned identity.
reloaded=false; confirmText='';
els.pitid.textContent='—';
context.loadPit('site-9','PIT-9');
await new Promise(resolve=>setTimeout(resolve,0));
check(fetched==='/api/load/site-9','Load Pit requests the immutable site_id route');
check(populated?.meta?.pit_id==='PIT-9','Load Pit restores the selected form payload');
check(vm.runInContext('_loaded_site_id',context)==='site-9','Load Pit enters edit mode for the returned site_id');


// Replacing an unarchived draft never strands its durable photo queue.
context.setRecordMode(null,null,false);
els.pitid.textContent='DIRTY';
context._pendingTotal=()=>2;confirmText='';discarded=0;switched=null;
context.loadPit('site-draft-target','PIT-DRAFT-TARGET');
await new Promise(resolve=>setTimeout(resolve,0));
check(confirmText.includes('will be discarded'),'loading over an unarchived photo draft warns about discard');
check(discarded===1,'confirmed replacement explicitly clears the draft photo queue');
check(switched==='site-9','loaded record switches to its site-specific photo queue');

// Switching between archived pits preserves, rather than transfers, local photos.
context.setRecordMode('site-old','PIT-OLD',false);
els.pitid.textContent='DIRTY';
context._pendingTotal=()=>2;confirmText='';discarded=0;
context.loadPit('site-new','PIT-NEW');
await new Promise(resolve=>setTimeout(resolve,0));
check(confirmText.includes('remain associated with the pit currently open'),'switching pits explains queued photos remain with their original pit');
check(discarded===0,'switching archived pits does not delete the original pit queue');


// Without durable storage, switching would transfer a memory-only queue; block it.
context.setRecordMode('site-memory','PIT-MEMORY',false);
context._pendingTotal=()=>1;context.attachmentOutboxIsAvailable=()=>false;fetched='';
await context.loadPit('site-other-memory','PIT-OTHER');
await new Promise(resolve=>setTimeout(resolve,0));
check(fetched==='','memory-only queued photos block a pit switch instead of transferring');
delete context.attachmentOutboxIsAvailable;

if(fail){console.error(`${fail} record-workflow UI tests failed`);process.exit(1);}
console.log(`${pass} record-workflow UI tests passed`);
