/** Browser tests: manifest handoff and explicit server cancellation. */
import fs from 'node:fs';
import vm from 'node:vm';
import { webcrypto } from 'node:crypto';

let passed=0;
function check(cond,label){if(!cond)throw new Error(`FAIL ${label}`);passed++;console.log('PASS',label);}

const rows=new Map();
const adapter={
  async open(){return true;},
  async getByContext(key){return [...rows.values()].filter(r=>r.context_key===key).map(r=>({...r}));},
  async put(r){rows.set(r.queue_id,{...r});},async del(id){rows.delete(id);},
  async clearContext(key){for(const [id,r] of rows)if(r.context_key===key)rows.delete(id);},
  async rebind(oldKey,newKey){for(const [id,r] of rows)if(r.context_key===oldKey)rows.set(id,{...r,context_key:newKey});},
};
const storage=new Map();
let cancelOk=true,cancelCalls=[];
const context={
  console,Blob,File,crypto:webcrypto,setTimeout,clearTimeout,Date,Uint8Array,
  cryopitOutboxAdapter__test:adapter,
  _loaded_site_id:null,_pendingAttach:{sheet:[],pitwall:[],stratigraphy:[]},
  _attachInfo:{maxBytes:10*1024*1024},API:'',
  localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,String(v)),removeItem:k=>storage.delete(k)},
  navigator:{storage:{async persisted(){return true;},async persist(){return true;},async estimate(){return{quota:1e9,usage:0};}}},
  document:{getElementById(){return null;}},refreshAttachUI(){},repaintAttachmentQueue(){},
  attachMsg(){},
  async fetch(url,opts){cancelCalls.push({url,opts});return{ok:cancelOk,status:cancelOk?200:503,async json(){return cancelOk?{ok:true,cancelled:true}:{ok:false,msg:'server unavailable'};}};},
};
vm.createContext(context);
vm.runInContext(fs.readFileSync('cryopit/static/js/84_attachment_outbox.js','utf8'),context,{filename:'84_attachment_outbox.js'});
await context.initAttachmentOutbox();
const file=new File([new Uint8Array([1,2,3,4])],'layer.jpg',{type:'image/jpeg'});
await context.queueAttachmentFiles('stratigraphy',[file],{top:50,bottom:49,key:'050-049cm'});
const manifest=context.attachmentUploadManifest();
check(manifest.length===1,'durable queue produces one archive manifest row');
check(manifest[0].queue_id===context._pendingAttach.stratigraphy[0].queue_id,'manifest preserves queue_id');
check(manifest[0].category==='stratigraphy'&&manifest[0].top_cm===50&&manifest[0].bottom_cm===49,'manifest preserves category and layer interval');
check(manifest[0].sha256?.length===64&&manifest[0].size_bytes===4,'manifest carries checksum and size');

context._loaded_site_id='site-8';
await context.bindAttachmentQueueToSite('site-8');
const item=context._pendingAttach.stratigraphy[0];
cancelOk=false;
let removed=await context.removeQueuedAttachment(item,'stratigraphy');
check(removed===false&&rows.size===1&&context._pendingAttach.stratigraphy.length===1,'failed server cancellation keeps the durable local file');
cancelOk=true;
removed=await context.removeQueuedAttachment(item,'stratigraphy');
check(removed===true&&rows.size===0&&context._pendingAttach.stratigraphy.length===0,'confirmed cancellation removes the local outbox row');
check(cancelCalls.at(-1).url.includes('/api/attachment-queue/site-8/')&&cancelCalls.at(-1).url.endsWith('/cancel'),'cancellation targets site_id and queue_id');

// If the upload succeeded but its response was lost, cancellation correctly
// refuses to delete the stored attachment; that refusal still confirms the
// local recovery copy is safe to remove.
await context.queueAttachmentFiles('pitwall',[new File([new Uint8Array([8,8])],'stored.jpg',{type:'image/jpeg'})]);
await context.bindAttachmentQueueToSite('site-8');
context.fetch=async()=>({ok:false,status:409,async json(){return{ok:false,stored:true,msg:'already stored'};}});
const storedItem=context._pendingAttach.pitwall[0];
removed=await context.removeQueuedAttachment(storedItem,'pitwall');
check(removed===true&&rows.size===0,'server-confirmed stored attachment allows local recovery copy cleanup');

// Bulk discard cancels/removes each item individually. A later cancellation
// failure keeps only the unresolved local blob instead of erasing the queue.
await context.queueAttachmentFiles('pitwall',[
  new File([new Uint8Array([9])],'discard-a.jpg',{type:'image/jpeg'}),
  new File([new Uint8Array([10])],'discard-b.jpg',{type:'image/jpeg'})
]);
await context.bindAttachmentQueueToSite('site-8');
let bulkCall=0;
context.fetch=async()=>{bulkCall++;return bulkCall===1
  ?{ok:true,status:200,async json(){return{ok:true,cancelled:true};}}
  :{ok:false,status:503,async json(){return{ok:false,msg:'server unavailable'};}};};
let discardFailed=false;
try{await context.discardAttachmentQueue();}catch(e){discardFailed=true;}
check(discardFailed,'bulk discard reports an unresolved server cancellation');
check(context._pendingAttach.pitwall.length===1&&context._pendingAttach.pitwall[0].file.name==='discard-b.jpg','bulk discard retains only the unresolved local photograph');
rows.clear();context._pendingAttach.pitwall.length=0;

// The archive button registers metadata before it sends the form payload.
let sent=null;
const apiContext={
  console,setTimeout,clearTimeout,AbortController,API:'',_archiveBusy:false,_loaded_site_id:null,
  async awaitAttachmentQueueReady(){},attachmentUploadManifest(){return[{queue_id:'q'}];},
  validate(){return{p:{meta:{pit_id:'P'}},e:[]};},_archive(p){sent=p;},
  document:{getElementById(){return null;}},window:{},localStorage:{},location:{},
};
vm.createContext(apiContext);
vm.runInContext(fs.readFileSync('cryopit/static/js/60_api.js','utf8'),apiContext,{filename:'60_api.js'});
apiContext.validate=()=>({p:{meta:{pit_id:'P'}},e:[]});
apiContext._archive=p=>{sent=p;};
await apiContext.doArchive();
check(sent&&sent.attachment_manifest?.[0].queue_id==='q','Archive sends the expected-photo manifest with the pit payload');

// A pending expectation from another/lost browser remains visible after loading.
const listEl={innerHTML:''},countEl={textContent:''},pipEl={classList:{toggle(){}}};
const uiContext={
  console,setTimeout,clearTimeout,API:'',_loaded_site_id:'site-8',
  __LIM_JSON__:{sheet:3,pitwall:6,stratigraphy:20},
  __LIM_TOTAL__:150,__LIM_STRAT__:20,__LIM_BYTES__:10*1024*1024,
  _pendingAttach:{sheet:[],pitwall:[],stratigraphy:[]},
  _attachInfo:{counts:{},limits:{sheet:3,pitwall:6,stratigraphy:20},uploads:[{
    queue_id:'11111111-1111-4111-8111-111111111111',category:'pitwall',filename:'lost.jpg',status:'pending'
  }],stratPerLayer:20,total:150},
  INST:[],esc:s=>String(s),syncChecklistFromEvidence(){},renderAttachmentOutboxState(){},
  refreshLayerCams(){},loadAttachList(){uiContext.loaded=(uiContext.loaded||0)+1;},setst(){},attachMsg(msg,kind){uiContext.lastMsg={msg,kind};},num:v=>Number(v),
  confirm(){return true;},
  async fetch(url,opts){(uiContext.deleteCalls=uiContext.deleteCalls||[]).push({url,opts});return{ok:true,status:200,async json(){return{ok:true,deleted:true};}};},
  confirm(){return true;},
  document:{
    getElementById(id){return id==='attach-list'?listEl:id==='att-cnt'?countEl:id==='p11'?pipEl:null;},
    querySelectorAll(){return[];},createElement(){return{};}
  },window:{addEventListener(){}},
};
vm.createContext(uiContext);
vm.runInContext(fs.readFileSync('cryopit/static/js/85_attachments.js','utf8'),uiContext,{filename:'85_attachments.js'});
vm.runInContext(`_attachInfo={counts:{},limits:{sheet:3,pitwall:6,stratigraphy:20},uploads:[{
  queue_id:'11111111-1111-4111-8111-111111111111',category:'pitwall',filename:'lost.jpg',status:'pending'
}],stratPerLayer:20,total:150};`,uiContext);
uiContext.renderAttachList({attachments:[],counts:{},limits:{sheet:3,pitwall:6,stratigraphy:20}});
check(listEl.innerHTML.includes('expected · unavailable here'),'server-only expected photo is visible in the attachment UI');
check(countEl.textContent.includes('1 expected on server'),'attachment summary reports server-only expectations');

uiContext.renderAttachList({attachments:[{attachment_id:7,category:'pitwall',filename:'stored.jpg',storage_status:'stored'}],counts:{pitwall:1},limits:{sheet:3,pitwall:6,stratigraphy:20}});
check(listEl.innerHTML.includes("deleteStoredAttachment('7','stored.jpg')"),'stored attachment renders an explicit delete control');
await uiContext.deleteStoredAttachment('7','stored.jpg');
check(uiContext.deleteCalls.some(c=>c.url==='/api/attachment/site-8/7/delete'&&c.opts?.method==='POST'),'stored attachment deletion targets immutable site and attachment IDs');
check(uiContext.deleteCalls.some(c=>c.url==='/api/attachments/site-8'),'successful deletion refreshes the server attachment list');

console.log(`${passed} browser manifest tests passed`);
