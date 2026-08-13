/* Lightweight unit tests load modules independently; the assembled app gets
   the CSRF-aware implementation from 00_core.js. */
if(typeof apiFetch==='undefined'){var apiFetch=(path,options)=>fetch((typeof API==='undefined'?'':API)+path,options);}

// -------------------------------------------------------------------------
// DURABLE PHOTO OUTBOX
//
// Selected files are first written to IndexedDB. IndexedDB is not the archive;
// it is a browser-side outbox that survives refreshes, browser restarts and a
// temporary loss of connectivity. The server remains authoritative. A local
// record is deleted only after the server confirms that the file is stored (or
// confirms it was already present as a byte-identical duplicate).
// -------------------------------------------------------------------------
const ATTACH_OUTBOX_DB='cryopit-photo-outbox';
const ATTACH_OUTBOX_VERSION=1;
const ATTACH_OUTBOX_STORE='photos';
const ATTACH_DRAFT_KEY='cp-attachment-draft-id';

let _attachmentContextKey=null;
let _attachmentOutboxAvailable=true;
let _attachmentOutboxPersistence='unknown'; // persistent | best-effort | unavailable
let _attachmentOutboxReady=Promise.resolve();
let _attachmentWriteBarrier=Promise.resolve();
let _attachmentPrepareBarrier=Promise.resolve();
let _attachmentUploadBusy=false;
let _attachmentRestoring=false;

function _attachmentUuid(){
  if(globalThis.crypto&&typeof globalThis.crypto.randomUUID==='function')return globalThis.crypto.randomUUID();
  const a=new Uint8Array(16);
  if(globalThis.crypto&&typeof globalThis.crypto.getRandomValues==='function')globalThis.crypto.getRandomValues(a);
  else for(let i=0;i<a.length;i++)a[i]=Math.floor(Math.random()*256);
  a[6]=(a[6]&15)|64;a[8]=(a[8]&63)|128;
  return [...a].map((n,i)=>n.toString(16).padStart(2,'0')+([3,5,7,9].includes(i)?'-':'')).join('');
}
function _attachmentDraftId(){
  try{
    let id=localStorage.getItem(ATTACH_DRAFT_KEY);
    if(!id){id=_attachmentUuid();localStorage.setItem(ATTACH_DRAFT_KEY,id);}
    return id;
  }catch(e){
    if(!_attachmentDraftId._volatile)_attachmentDraftId._volatile=_attachmentUuid();
    return _attachmentDraftId._volatile;
  }
}
function rotateAttachmentDraftId(){
  try{localStorage.removeItem(ATTACH_DRAFT_KEY);}catch(e){}
  _attachmentDraftId._volatile=null;
  return _attachmentDraftId();
}
function attachmentContextKey(siteId=_loaded_site_id){
  return siteId?'site:'+siteId:'draft:'+_attachmentDraftId();
}

// Adapter seam: browser production uses native IndexedDB. Lightweight tests
// inject cryopitOutboxAdapter__test with the same methods and need no jsdom or
// third-party fake-indexeddb package.
function _nativeOutboxAdapter(){
  let dbp=null;
  function open(){
    if(dbp)return dbp;
    dbp=new Promise((resolve,reject)=>{
      if(!globalThis.indexedDB){reject(new Error('IndexedDB is unavailable'));return;}
      const req=indexedDB.open(ATTACH_OUTBOX_DB,ATTACH_OUTBOX_VERSION);
      req.onupgradeneeded=()=>{
        const db=req.result;
        let store;
        if(!db.objectStoreNames.contains(ATTACH_OUTBOX_STORE)){
          store=db.createObjectStore(ATTACH_OUTBOX_STORE,{keyPath:'queue_id'});
        }else store=req.transaction.objectStore(ATTACH_OUTBOX_STORE);
        if(!store.indexNames.contains('context_key'))store.createIndex('context_key','context_key',{unique:false});
      };
      req.onsuccess=()=>resolve(req.result);
      req.onerror=()=>reject(req.error||new Error('Could not open IndexedDB'));
      req.onblocked=()=>reject(new Error('IndexedDB upgrade is blocked by another CryoPit tab'));
    });
    return dbp;
  }
  function reqp(req){return new Promise((resolve,reject)=>{req.onsuccess=()=>resolve(req.result);req.onerror=()=>reject(req.error||new Error('IndexedDB request failed'));});}
  async function getByContext(contextKey){
    const db=await open();
    const tx=db.transaction(ATTACH_OUTBOX_STORE,'readonly');
    const req=tx.objectStore(ATTACH_OUTBOX_STORE).index('context_key').getAll(contextKey);
    return reqp(req);
  }
  async function summarizeAll(){
    const db=await open();
    const tx=db.transaction(ATTACH_OUTBOX_STORE,'readonly');
    const store=tx.objectStore(ATTACH_OUTBOX_STORE);
    const total=reqp(store.count());
    // Count distinct contexts with a key cursor. This deliberately avoids
    // getAll(): queued records contain full-size File blobs, and the workspace
    // needs counts only, not every photograph loaded into memory.
    const contexts=new Promise((resolve,reject)=>{
      let count=0;
      const req=store.index('context_key').openKeyCursor(null,'nextunique');
      req.onsuccess=()=>{const cursor=req.result;if(!cursor){resolve(count);return;}count++;cursor.continue();};
      req.onerror=()=>reject(req.error||new Error('IndexedDB context summary failed'));
    });
    const [totalCount,contextCount]=await Promise.all([total,contexts]);
    return{total:totalCount,contexts:contextCount};
  }
  async function put(record){
    const db=await open();
    const tx=db.transaction(ATTACH_OUTBOX_STORE,'readwrite');
    tx.objectStore(ATTACH_OUTBOX_STORE).put(record);
    await new Promise((resolve,reject)=>{tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error);});
  }
  async function del(queueId){
    const db=await open();
    const tx=db.transaction(ATTACH_OUTBOX_STORE,'readwrite');
    tx.objectStore(ATTACH_OUTBOX_STORE).delete(queueId);
    await new Promise((resolve,reject)=>{tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error);});
  }
  async function clearContext(contextKey){
    const rows=await getByContext(contextKey);
    const db=await open();
    const tx=db.transaction(ATTACH_OUTBOX_STORE,'readwrite');
    const store=tx.objectStore(ATTACH_OUTBOX_STORE);
    rows.forEach(r=>store.delete(r.queue_id));
    await new Promise((resolve,reject)=>{tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error);});
  }
  async function rebind(oldKey,newKey){
    const rows=await getByContext(oldKey);
    const db=await open();
    const tx=db.transaction(ATTACH_OUTBOX_STORE,'readwrite');
    const store=tx.objectStore(ATTACH_OUTBOX_STORE);
    rows.forEach(r=>store.put({...r,context_key:newKey,updated_at:new Date().toISOString()}));
    await new Promise((resolve,reject)=>{tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error);});
  }
  return{open,getByContext,summarizeAll,put,del,clearContext,rebind};
}
// Deliberately NOT spelled with leading-and-trailing double underscores in
// upper case. The page is assembled by substituting tokens of exactly that
// shape, and test_dom.mjs refuses to serve a page still containing one — a
// guard worth having, since an unsubstituted token reaches the browser as
// broken JS. Naming this seam that way made the guard fire on legitimate code
// and exit before running a single assertion, so the whole DOM suite was dead
// from the moment this file was added and every stage after it was written
// against a suite that silently ran nothing.
const _attachmentOutboxAdapter=globalThis.cryopitOutboxAdapter__test||_nativeOutboxAdapter();

function _queueWrite(work){
  const result=_attachmentWriteBarrier.then(work,work);
  // The barrier serializes later writes but does not permanently poison the
  // queue after one failed transaction. Callers still receive `result` and can
  // surface that specific failure.
  _attachmentWriteBarrier=result.catch(()=>{});
  return result;
}
async function awaitAttachmentQueueReady(){
  await _attachmentOutboxReady;
  await _attachmentPrepareBarrier;
  await _attachmentWriteBarrier;
}
function attachmentQueueIsBusy(){return _attachmentUploadBusy;}
function attachmentOutboxIsAvailable(){return _attachmentOutboxAvailable;}

async function _requestPersistentAttachmentStorage(){
  if(!navigator.storage){_attachmentOutboxPersistence='best-effort';return false;}
  try{
    if(await navigator.storage.persisted()){
      _attachmentOutboxPersistence='persistent';return true;
    }
    if(typeof navigator.storage.persist==='function'&&await navigator.storage.persist()){
      _attachmentOutboxPersistence='persistent';return true;
    }
    _attachmentOutboxPersistence='best-effort';return false;
  }catch(e){_attachmentOutboxPersistence='best-effort';return false;}
}
async function _checkAttachmentQuota(bytes){
  if(!navigator.storage||typeof navigator.storage.estimate!=='function')return;
  const est=await navigator.storage.estimate();
  if(est.quota==null||est.usage==null)return;
  const free=est.quota-est.usage;
  // Leave a modest cushion for browser bookkeeping and the form draft.
  if(free<bytes+2*1024*1024){
    throw new Error(`Not enough browser storage to queue this file (${Math.ceil(bytes/1048576)} MB needed).`);
  }
}
async function _attachmentSha256(file){
  if(!globalThis.crypto||!crypto.subtle||typeof file.arrayBuffer!=='function')return null;
  const digest=await crypto.subtle.digest('SHA-256',await file.arrayBuffer());
  return [...new Uint8Array(digest)].map(b=>b.toString(16).padStart(2,'0')).join('');
}
function _recordToPending(record){
  return{
    queue_id:record.queue_id,
    context_key:record.context_key,
    file:record.file,
    top:record.top_cm,
    bottom:record.bottom_cm,
    key:record.layer_key||'',
    checksum:record.checksum||null,
    status:(record.status==='uploading'||record.status==='saving')?'queued':(record.status||'queued'),
    error:record.error||'',
    created_at:record.created_at,
  };
}
function _pendingToRecord(item,category){
  const f=item.file||item;
  return{
    queue_id:item.queue_id,
    context_key:item.context_key||_attachmentContextKey,
    category,
    file:f,
    filename:f.name||'',
    mime_type:f.type||'',
    size:f.size||0,
    last_modified:f.lastModified||0,
    top_cm:item.top==null?null:item.top,
    bottom_cm:item.bottom==null?null:item.bottom,
    layer_key:item.key||'',
    checksum:item.checksum||null,
    status:item.status||'queued',
    error:item.error||'',
    created_at:item.created_at||new Date().toISOString(),
    updated_at:new Date().toISOString(),
  };
}
function _replacePendingFromRecords(records){
  _pendingAttach.sheet.length=0;
  _pendingAttach.pitwall.length=0;
  _pendingAttach.stratigraphy.length=0;
  records.sort((a,b)=>String(a.created_at).localeCompare(String(b.created_at))).forEach(r=>{
    if(!_pendingAttach[r.category])return;
    const item=_recordToPending(r);
    _pendingAttach[r.category].push(item);
    if(item.status!==r.status){
      _queueWrite(()=>_attachmentOutboxAdapter.put(_pendingToRecord(item,r.category))).catch(()=>{});
    }
  });
}
async function restoreAttachmentQueue(siteId=_loaded_site_id,{announce=true}={}){
  _attachmentContextKey=attachmentContextKey(siteId);
  if(!_attachmentOutboxAvailable){(typeof repaintAttachmentQueue==='function'?repaintAttachmentQueue():refreshAttachUI());return 0;}
  _attachmentRestoring=true;
  try{
    const rows=await _attachmentOutboxAdapter.getByContext(_attachmentContextKey);
    _replacePendingFromRecords(rows||[]);
    if(announce&&rows&&rows.length)attachMsg(`${rows.length} photograph${rows.length===1?'':'s'} restored from this browser.`,'ok');
    (typeof repaintAttachmentQueue==='function'?repaintAttachmentQueue():refreshAttachUI());
    return rows?rows.length:0;
  }finally{_attachmentRestoring=false;}
}
async function switchAttachmentQueueContext(siteId){
  await awaitAttachmentQueueReady();
  return restoreAttachmentQueue(siteId,{announce:true});
}
async function bindAttachmentQueueToSite(siteId){
  await awaitAttachmentQueueReady();
  const oldKey=_attachmentContextKey||attachmentContextKey(null);
  const newKey=attachmentContextKey(siteId);
  if(oldKey!==newKey&&_attachmentOutboxAvailable){
    await _attachmentOutboxAdapter.rebind(oldKey,newKey);
  }
  _attachmentContextKey=newKey;
  for(const category of ['sheet','pitwall','stratigraphy']){
    _pendingAttach[category].forEach(it=>{it.context_key=newKey;});
  }
}
async function discardAttachmentQueue({rotateDraft=false}={}){
  await awaitAttachmentQueueReady();
  // For a loaded pit, deleting the local recovery bytes is also an explicit
  // cancellation of every expectation this browser owns. Process items one at
  // a time so a network failure never erases a blob whose server state is
  // unknown. Successfully cancelled/confirmed items may be removed even when a
  // later item fails; the caller then remains on the current pit with only the
  // unresolved files still visible.
  const failures=[];
  for(const category of ['sheet','pitwall','stratigraphy']){
    for(const item of [..._pendingAttach[category]]){
      const ok=await removeQueuedAttachment(item,category);
      if(!ok)failures.push((item.file||item).name||item.queue_id||'photograph');
    }
  }
  if(failures.length)throw new Error(`${failures.length} queued photograph${failures.length===1?'':'s'} could not be safely discarded.`);
  const key=_attachmentContextKey||attachmentContextKey();
  if(_attachmentOutboxAvailable)await _attachmentOutboxAdapter.clearContext(key);
  if(rotateDraft)rotateAttachmentDraftId();
  (typeof repaintAttachmentQueue==='function'?repaintAttachmentQueue():refreshAttachUI());
}
async function removeQueuedAttachment(item,category){
  try{await cancelExpectedAttachment(item);}
  catch(e){attachMsg('Could not cancel the server-side expectation: '+e.message+' The local copy was kept.','err');return false;}
  if(item&&item.queue_id&&_attachmentOutboxAvailable){
    try{await _queueWrite(()=>_attachmentOutboxAdapter.del(item.queue_id));}
    catch(e){attachMsg('Could not remove the local queued copy: '+e.message,'err');return false;}
  }
  const q=_pendingAttach[category]||[];
  const i=q.indexOf(item);if(i>=0)q.splice(i,1);
  (typeof repaintAttachmentQueue==='function'?repaintAttachmentQueue():refreshAttachUI());
  return true;
}
async function updateQueuedAttachment(item,category){
  if(!item||!item.queue_id||!_attachmentOutboxAvailable)return;
  await _queueWrite(()=>_attachmentOutboxAdapter.put(_pendingToRecord(item,category)));
}
async function confirmQueuedAttachment(item,category){
  if(item&&item.queue_id&&_attachmentOutboxAvailable){
    await _queueWrite(()=>_attachmentOutboxAdapter.del(item.queue_id));
  }
  const q=_pendingAttach[category]||[];
  const i=q.findIndex(x=>x===item||x.queue_id===item.queue_id);if(i>=0)q.splice(i,1);
}

async function _queueAttachmentFilesImpl(category,files,meta={}){
  await _attachmentOutboxReady;
  await _requestPersistentAttachmentStorage();
  const added=[],duplicates=[],failed=[];
  for(const file of files){
    const item={
      queue_id:_attachmentUuid(),context_key:_attachmentContextKey||attachmentContextKey(),file,
      top:meta.top==null?null:meta.top,bottom:meta.bottom==null?null:meta.bottom,
      key:meta.key||'',checksum:null,status:'saving',error:'',created_at:new Date().toISOString(),
    };
    _pendingAttach[category].push(item);(typeof repaintAttachmentQueue==='function'?repaintAttachmentQueue():refreshAttachUI());
    try{
      if(_attachInfo.maxBytes&&file.size>_attachInfo.maxBytes){
        throw new Error(`${file.name} is larger than the ${Math.floor(_attachInfo.maxBytes/1048576)} MB file limit.`);
      }
      await _checkAttachmentQuota(file.size||0);
      item.checksum=await _attachmentSha256(file);
      const duplicate=[..._pendingAttach.sheet,..._pendingAttach.pitwall,..._pendingAttach.stratigraphy]
        .find(x=>x!==item&&item.checksum&&x.checksum===item.checksum);
      if(duplicate){
        const q=_pendingAttach[category],i=q.indexOf(item);if(i>=0)q.splice(i,1);
        duplicates.push(file.name);(typeof repaintAttachmentQueue==='function'?repaintAttachmentQueue():refreshAttachUI());continue;
      }
      item.status=_attachmentOutboxAvailable?'queued':'volatile';
      if(!_attachmentOutboxAvailable)item.error='IndexedDB unavailable; this file will be lost if the page closes.';
      if(_attachmentOutboxAvailable)await _queueWrite(()=>_attachmentOutboxAdapter.put(_pendingToRecord(item,category)));
      added.push(item);
    }catch(e){
      const q=_pendingAttach[category],i=q.indexOf(item);if(i>=0)q.splice(i,1);
      failed.push({name:file.name,msg:e.message});
      (typeof repaintAttachmentQueue==='function'?repaintAttachmentQueue():refreshAttachUI());
    }
  }
  return{added,duplicates,failed};
}


function queueAttachmentFiles(category,files,meta={}){
  const run=()=>_queueAttachmentFilesImpl(category,files,meta);
  const work=_attachmentPrepareBarrier.then(run,run);
  _attachmentPrepareBarrier=work.catch(()=>{});
  return work;
}

function attachmentUploadManifest(){
  const out=[];
  for(const category of ['sheet','pitwall','stratigraphy']){
    for(const item of _pendingAttach[category]){
      const f=item.file||item;
      out.push({
        queue_id:item.queue_id,category,filename:f.name||'',mime_type:f.type||'',
        size_bytes:Number.isFinite(f.size)?f.size:null,sha256:item.checksum||null,
        top_cm:item.top==null?null:item.top,bottom_cm:item.bottom==null?null:item.bottom,
      });
    }
  }
  return out;
}

async function cancelExpectedAttachment(item){
  if(!_loaded_site_id||!item||!item.queue_id)return{ok:true,absent:true};
  const response=await apiFetch('/api/attachment-queue/'+encodeURIComponent(_loaded_site_id)
    +'/'+encodeURIComponent(item.queue_id)+'/cancel',{method:'POST'});
  const result=await response.json().catch(()=>({ok:false,msg:'cancel failed ('+response.status+')'}));
  // A lost success response can leave a local recovery copy after the server
  // already stored the attachment. That is safe to remove locally even though
  // the server correctly refuses to cancel the completed attachment itself.
  if(result.stored)return{...result,ok:true,confirmedStored:true};
  if(!response.ok||!result.ok)throw new Error(result.msg||'Could not cancel expected photograph.');
  return result;
}

function attachmentOutboxSummary(){
  const all=[..._pendingAttach.sheet,..._pendingAttach.pitwall,..._pendingAttach.stratigraphy];
  const counts={saving:0,queued:0,uploading:0,failed:0,volatile:0};
  all.forEach(x=>{counts[x.status]!==undefined?counts[x.status]++:counts.queued++;});
  return{...counts,total:all.length,persistence:_attachmentOutboxPersistence,available:_attachmentOutboxAvailable};
}
async function attachmentOutboxAllSummary(){
  await _attachmentOutboxReady;
  if(!_attachmentOutboxAvailable){
    return{total:_pendingTotal(),contexts:_pendingTotal()?1:0,
      persistence:_attachmentOutboxPersistence,available:false};
  }
  if(typeof _attachmentOutboxAdapter.summarizeAll!=='function'){
    const current=attachmentOutboxSummary();
    return{...current,contexts:current.total?1:0};
  }
  const summary=await _attachmentOutboxAdapter.summarizeAll();
  return{total:summary.total||0,contexts:summary.contexts||0,
    persistence:_attachmentOutboxPersistence,available:true};
}
function renderAttachmentOutboxState(){
  const el=document.getElementById('attach-outbox-state');if(!el)return;
  const s=attachmentOutboxSummary();
  if(!s.available){el.textContent='Local recovery storage unavailable · queued photos survive only while this page stays open';el.className='att-outbox-state warn';return;}
  const bits=[];
  if(s.saving)bits.push(`${s.saving} saving locally`);
  if(s.queued)bits.push(`${s.queued} safely queued`);
  if(s.uploading)bits.push(`${s.uploading} uploading`);
  if(s.failed)bits.push(`${s.failed} failed — retry on Archive`);
  if(s.volatile)bits.push(`${s.volatile} not durably queued`);
  if(!bits.length){el.textContent='';el.className='att-outbox-state';return;}
  const storage=s.persistence==='persistent'?'persistent browser storage':
    s.persistence==='best-effort'?'browser storage (best effort)':'browser storage';
  el.textContent=bits.join(' · ')+' · '+storage;
  el.className='att-outbox-state'+((s.failed||s.volatile)?' warn':'');
}

function initAttachmentOutbox(){
  _attachmentContextKey=attachmentContextKey();
  _attachmentOutboxReady=(async()=>{
    try{
      await _attachmentOutboxAdapter.open();
      _attachmentOutboxAvailable=true;
      try{
        if(navigator.storage&&await navigator.storage.persisted())_attachmentOutboxPersistence='persistent';
        else _attachmentOutboxPersistence='best-effort';
      }catch(e){_attachmentOutboxPersistence='best-effort';}
      await restoreAttachmentQueue(_loaded_site_id,{announce:true});
    }catch(e){
      _attachmentOutboxAvailable=false;_attachmentOutboxPersistence='unavailable';
      renderAttachmentOutboxState();
      attachMsg('Durable browser photo queue is unavailable. Selected photos will not survive a page close.','err');
    }
  })().finally(()=>{if(typeof refreshWorkspacePhotoQueue==='function')refreshWorkspacePhotoQueue();});
  return _attachmentOutboxReady;
}
