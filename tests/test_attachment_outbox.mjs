/** Lightweight tests for the real durable attachment-outbox module.
 * No jsdom or fake-indexeddb dependency is required: the module's documented
 * adapter seam is backed by an in-memory map while all queue/state logic is the
 * production code.
 */
import fs from 'node:fs';
import vm from 'node:vm';
import { webcrypto } from 'node:crypto';

let passed=0;
function check(cond,label){
  if(!cond)throw new Error(`FAIL ${label}`);
  passed++;console.log('PASS',label);
}

const rows=new Map();
const adapter={
  async open(){return true;},
  async getByContext(key){return [...rows.values()].filter(r=>r.context_key===key).map(r=>({...r}));},
  async put(record){rows.set(record.queue_id,{...record});},
  async del(id){rows.delete(id);},
  async clearContext(key){for(const [id,r] of rows)if(r.context_key===key)rows.delete(id);},
  async rebind(oldKey,newKey){for(const [id,r] of rows)if(r.context_key===oldKey)rows.set(id,{...r,context_key:newKey});},
};
const storageMap=new Map();
const outboxEl={textContent:'',className:''};
let quota=1024*1024*1024,usage=0;
const context={
  console,Blob,File,crypto:webcrypto,setTimeout,clearTimeout,Date,Uint8Array,
  cryopitOutboxAdapter__test:adapter,
  _loaded_site_id:null,API:'',
  _pendingAttach:{sheet:[],pitwall:[],stratigraphy:[]},
  _attachInfo:{maxBytes:10*1024*1024},
  localStorage:{
    getItem:k=>storageMap.has(k)?storageMap.get(k):null,
    setItem:(k,v)=>storageMap.set(k,String(v)),removeItem:k=>storageMap.delete(k),
  },
  navigator:{storage:{
    async persisted(){return false;},async persist(){return true;},
    async estimate(){return{quota,usage};},
  }},
  document:{getElementById:id=>id==='attach-outbox-state'?outboxEl:null},
  refreshAttachUI(){},repaintAttachmentQueue(){},
  attachMsg(){},
  async fetch(){return{ok:true,status:200,async json(){return{ok:true,absent:true};}};},
};
vm.createContext(context);
vm.runInContext(fs.readFileSync('cryopit/static/js/84_attachment_outbox.js','utf8'),context,{filename:'84_attachment_outbox.js'});

await context.initAttachmentOutbox();
check(vm.runInContext('_attachmentOutboxAvailable',context)===true,'IndexedDB adapter initializes');
check(vm.runInContext('_attachmentOutboxPersistence',context)==='best-effort','initial persistence state is detected');

const f1=new File([new Uint8Array([1,2,3,4])],'wall.jpg',{type:'image/jpeg',lastModified:10});
let result=await context.queueAttachmentFiles('pitwall',[f1]);
check(result.added.length===1 && context._pendingAttach.pitwall.length===1,'selected file enters visible queue');
check(rows.size===1,'selected file is durably stored');
check(context._pendingAttach.pitwall[0].status==='queued','durably stored file becomes queued');
check(context._pendingAttach.pitwall[0].checksum?.length===64,'SHA-256 is recorded');
check(vm.runInContext('_attachmentOutboxPersistence',context)==='persistent','persistent-storage grant is recorded');

// Simulated page/browser restart: memory disappears, durable row survives.
context._pendingAttach.pitwall.length=0;
await context.restoreAttachmentQueue(null,{announce:false});
check(context._pendingAttach.pitwall.length===1,'queue restores after browser-memory loss');
check(context._pendingAttach.pitwall[0].file.name==='wall.jpg','restored queue retains original File');

// Interrupted saving/uploading states normalize to retryable queued on restore.
const restored=context._pendingAttach.pitwall[0];
restored.status='uploading';
await context.updateQueuedAttachment(restored,'pitwall');
context._pendingAttach.pitwall.length=0;
await context.restoreAttachmentQueue(null,{announce:false});
check(context._pendingAttach.pitwall[0].status==='queued','interrupted upload restores as queued');

// First archive rebinds the draft outbox to the immutable site identity.
await context.bindAttachmentQueueToSite('site-123');
check([...rows.values()][0].context_key==='site:site-123','draft queue rebinds to site_id');
context._loaded_site_id='site-other';
await context.switchAttachmentQueueContext('site-other');
check(context._pendingAttach.pitwall.length===0,'another pit does not inherit queued photos');
await context.switchAttachmentQueueContext('site-123');
check(context._pendingAttach.pitwall.length===1,'returning to pit restores its own queue');

// A server failure is persisted rather than discarded.
const item=context._pendingAttach.pitwall[0];
item.status='failed';item.error='network down';
await context.updateQueuedAttachment(item,'pitwall');
context._pendingAttach.pitwall.length=0;
await context.restoreAttachmentQueue('site-123',{announce:false});
check(context._pendingAttach.pitwall[0].status==='failed','failed upload remains queued across restart');
check(context._pendingAttach.pitwall[0].error==='network down','failed upload retains its error');

// Server confirmation removes both local copies.
await context.confirmQueuedAttachment(context._pendingAttach.pitwall[0],'pitwall');
check(context._pendingAttach.pitwall.length===0 && rows.size===0,'confirmed storage deletes IndexedDB outbox row');

// Duplicate bytes are skipped locally, even under a different filename.
const sameA=new File([new Uint8Array([9,8,7])],'a.jpg',{type:'image/jpeg'});
const sameB=new File([new Uint8Array([9,8,7])],'b.jpg',{type:'image/jpeg'});
result=await context.queueAttachmentFiles('sheet',[sameA]);
const duplicate=await context.queueAttachmentFiles('pitwall',[sameB]);
check(result.added.length===1 && duplicate.duplicates.length===1,'byte-identical duplicate selection is skipped');
check(rows.size===1,'duplicate selection does not consume a durable row');

// Archive waits for a selection that is still hashing/persisting.
let release;
const delayed={name:'slow.jpg',type:'image/jpeg',size:3,lastModified:0,
  arrayBuffer:()=>new Promise(resolve=>{release=()=>resolve(Uint8Array.from([5,5,5]).buffer);})};
const pending=context.queueAttachmentFiles('stratigraphy',[delayed],{top:30,bottom:29,key:'030-029cm'});
await new Promise(resolve=>setTimeout(resolve,0));
let ready=false;
const waiter=context.awaitAttachmentQueueReady().then(()=>{ready=true;});
await new Promise(resolve=>setTimeout(resolve,0));
check(!ready,'archive readiness waits for an in-progress local save');
release();await pending;await waiter;
check(ready && context._pendingAttach.stratigraphy.length===1,'archive readiness resolves after durable save');
check(context._pendingAttach.stratigraphy[0].top===30 && context._pendingAttach.stratigraphy[0].bottom===29,'layer depths survive the durable queue');

// Quota errors are explicit and do not leave a false queued record.
quota=100;usage=99;
const tooMuch=new File([new Uint8Array([1,2,3])],'quota.jpg',{type:'image/jpeg'});
const quotaResult=await context.queueAttachmentFiles('pitwall',[tooMuch]);
check(quotaResult.failed.length===1,'insufficient browser quota is surfaced');
check(!context._pendingAttach.pitwall.some(x=>x.file.name==='quota.jpg'),'quota failure leaves no misleading queue item');
quota=1024*1024*1024;usage=0;

const oldDraft=storageMap.get('cp-attachment-draft-id');
context._loaded_site_id='site-123';
await context.discardAttachmentQueue({rotateDraft:true});
check(rows.size===0 && context._pendingAttach.sheet.length===0 && context._pendingAttach.stratigraphy.length===0,'discard clears current durable queue');
check(storageMap.get('cp-attachment-draft-id')!==oldDraft,'Start New Pit rotates the draft identity');

console.log(`${passed} durable attachment-outbox tests passed`);
