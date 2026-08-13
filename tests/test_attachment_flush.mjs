/** Stage 7 upload lifecycle: failed server writes stay durable; confirmed
 * writes (including byte duplicates) remove the local outbox entry. */
import fs from 'node:fs';
import vm from 'node:vm';
import { webcrypto } from 'node:crypto';

let passed=0;
function check(cond,label){if(!cond)throw new Error(`FAIL ${label}`);passed++;console.log('PASS',label);}
const rows=new Map();
const adapter={
  async open(){},async getByContext(k){return [...rows.values()].filter(r=>r.context_key===k).map(r=>({...r}));},
  async put(r){rows.set(r.queue_id,{...r});},async del(id){rows.delete(id);},
  async clearContext(k){for(const[id,r]of rows)if(r.context_key===k)rows.delete(id);},
  async rebind(a,b){for(const[id,r]of rows)if(r.context_key===a)rows.set(id,{...r,context_key:b});},
};
const local=new Map();
let reply={ok:false,msg:'server refused'};
const context={
  console,Blob,File,FormData,crypto:webcrypto,setTimeout,clearTimeout,Date,Uint8Array,
  cryopitOutboxAdapter__test:adapter,_loaded_site_id:'site-1',API:'',
  localStorage:{getItem:k=>local.get(k)||null,setItem:(k,v)=>local.set(k,String(v)),removeItem:k=>local.delete(k)},
  navigator:{storage:{async persisted(){return true;},async persist(){return true;},async estimate(){return{quota:1e9,usage:0};}}},
  document:{getElementById(){return null;},querySelectorAll(){return[];}},
  addEventListener(){},
  fetchErr:e=>'error: '+e.message,
  fetch:async()=>({ok:!!reply.ok,status:reply.ok?200:409,headers:{get:()=>null},json:async()=>reply}),
  setst(){},attachMsg(){},syncChecklistFromEvidence(){},refreshStatusGlyphs(){},
  refreshLayerCams(){},tick(){},num:v=>Number(v),esc:s=>String(s),
};
context.window=context;
vm.createContext(context);
let attach=fs.readFileSync('cryopit/static/js/85_attachments.js','utf8')
  .replace('__LIM_JSON__','{"pitwall":6,"sheet":3,"stratigraphy":20}')
  .replace('__LIM_TOTAL__','150').replace('__LIM_STRAT__','20').replace('__LIM_BYTES__',String(10*1024*1024));
vm.runInContext(attach,context,{filename:'85_attachments.js'});
vm.runInContext(fs.readFileSync('cryopit/static/js/84_attachment_outbox.js','utf8'),context,{filename:'84_attachment_outbox.js'});
// Avoid background attachment-list fetches; this test isolates the outbox/upload contract.
context.loadAttachList=()=>{};context.refreshAttachUI=()=>{};context.refreshLayerCams=()=>{};
await context.initAttachmentOutbox();
await context.queueAttachmentFiles('pitwall',[new File([new Uint8Array([1,3,5])],'wall.jpg',{type:'image/jpeg'})]);
check(rows.size===1,'setup stores one durable queued file');
// A 429 is flow control, not rejection. Keep the bytes queued and schedule an
// automatic retry rather than frightening the field user with a failed state.
let automaticRetryMs=null;
context.setTimeout=(fn,ms)=>{automaticRetryMs=ms;return 1;};
context.clearTimeout=()=>{};
context.fetch=async()=>({ok:false,status:429,headers:{get:n=>n==='Retry-After'?'7':null},json:async()=>({ok:false,msg:'Too many requests; retry shortly.'})});
let result=await context.flushPendingAttachments();
check(result.throttled===true&&result.queued===1&&result.failed===0,'429 remains queued rather than failed');
check(rows.size===1&&vm.runInContext('_pendingAttach.pitwall[0].status',context)==='waiting','429 preserves durable queued state');
check(automaticRetryMs===7000,'429 Retry-After schedules automatic retry');

reply={ok:false,msg:'server refused'};
context.fetch=async()=>({ok:false,status:409,headers:{get:()=>null},json:async()=>reply});
result=await context.flushPendingAttachments();
check(result.failed===1,'server rejection is reported as pending failure');
check(rows.size===1,'server rejection keeps IndexedDB row');
check(vm.runInContext('_pendingAttach.pitwall[0].status',context)==='failed','server rejection marks visible item failed');

reply={ok:true,attachment_id:'att-1'};
context.fetch=async()=>({ok:true,status:200,headers:{get:()=>null},json:async()=>reply});
result=await context.flushPendingAttachments();
check(result.done===1,'retry can upload the failed item');
check(rows.size===0 && vm.runInContext('_pendingAttach.pitwall.length',context)===0,'confirmed upload deletes local queue copies');

await context.queueAttachmentFiles('pitwall',[new File([new Uint8Array([2,4,6])],'dup.jpg',{type:'image/jpeg'})]);
reply={ok:true,duplicate:true,attachment_id:'att-existing'};
result=await context.flushPendingAttachments();
check(result.duplicates.length===1,'server duplicate confirmation is surfaced');
check(rows.size===0 && vm.runInContext('_pendingAttach.pitwall.length',context)===0,'confirmed duplicate also clears local outbox');

await context.queueAttachmentFiles('pitwall',[new File([new Uint8Array([7,7])],'network.jpg',{type:'image/jpeg'})]);
context.fetch=async()=>{throw new Error('offline');};
result=await context.flushPendingAttachments();
check(result.failed===1 && rows.size===1,'network failure retains durable queue row');
check(vm.runInContext('_pendingAttach.pitwall[0].error',context).includes('offline'),'network failure retains useful error');

console.log(`${passed} attachment upload/outbox tests passed`);
