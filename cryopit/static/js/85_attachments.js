/* Lightweight unit tests load modules independently; the assembled app gets
   the CSRF-aware implementation from 00_core.js. */
if(typeof apiFetch==='undefined'){var apiFetch=(path,options)=>fetch((typeof API==='undefined'?'':API)+path,options);}

// -------------------------------------------------------------------------
// Attachments (§9): pit sheet (always) + pit-wall / stratigraphy photos
// (enabled only when their checklist rows are Y). Uploads require the pit to
// be archived first and never block archiving. Selected image bytes are kept
// at their original resolution; HEIC conversion, when available, happens on
// the server without resizing.
// -------------------------------------------------------------------------
// (a former _pitwallIdx() helper lived here; it was unused AND wrong — it
// returned the INST array index, not the Y/N-row index the DOM ids use.
// _checklistYes() below does the counting correctly.)
function attachmentsEnabled(){
  return _loaded_site_id!==null;
}
function _checklistYes(name){
  let di=0,found=null;
  INST.forEach(it=>{if(it.g)return;const i=di++;if(it.n===name)found=i;});
  return found!==null&&document.getElementById('yy'+found)?.classList.contains('on');
}
// Written in from _ATTACH_LIMITS at page assembly, not typed here. This used
// to be a hand-kept copy of the server's caps; the /api/attachments response
// still overwrites it, but the initial render (before that call returns) now
// shows the real numbers instead of a stale guess.
let _attachInfo={counts:{},limits:__LIM_JSON__,total:__LIM_TOTAL__,stratPerLayer:__LIM_STRAT__,maxBytes:__LIM_BYTES__};
// In-memory view of the durable IndexedDB outbox. Every entry is an object
// carrying queue_id, File, category metadata and a queue status. The actual
// recovery copy lives in IndexedDB until the server confirms storage.
let _pendingAttach={sheet:[],pitwall:[],stratigraphy:[]};
function attachMsg(msg,kind){
  // §12-local message line: attachment problems belong next to the inputs,
  // not in a global toast (which stays for whole-pit operations)
  const el=document.getElementById('attach-msg');
  if(!el)return setst(msg,kind);
  el.textContent=msg;
  el.className='attach-msg'+(kind?' '+kind:'');
  clearTimeout(el._tm);
  if(kind!=='err')el._tm=setTimeout(()=>{el.textContent='';el.className='attach-msg';},8000);
}
async function removePending(category,idx){
  // Removal deletes both the visible item and its durable IndexedDB copy.
  const item=_pendingAttach[category][idx];
  if(item)await removeQueuedAttachment(item,category);
}
function _pendingTotal(){return _pendingAttach.sheet.length+_pendingAttach.pitwall.length+_pendingAttach.stratigraphy.length;}
function _localQueueIds(){return new Set([..._pendingAttach.sheet,..._pendingAttach.pitwall,..._pendingAttach.stratigraphy].map(x=>x.queue_id));}
function _serverPending(){
  const local=_localQueueIds();
  return (_attachInfo.uploads||[]).filter(x=>x.status==='pending'&&!local.has(x.queue_id));
}
function _serverPendingFor(category){return _serverPending().filter(x=>x.category===category);}
function repaintAttachmentQueue(){
  if(typeof refreshWorkspacePhotoQueue==='function')refreshWorkspacePhotoQueue();
  if(typeof syncChecklistFromEvidence==='function')syncChecklistFromEvidence();
  if(typeof renderAttachmentOutboxState==='function')renderAttachmentOutboxState();
  renderAttachList({attachments:_attachInfo.attachments||[],counts:_attachInfo.counts||{},
    limits:_attachInfo.limits||{},strat_per_layer:_attachInfo.stratPerLayer,total_limit:_attachInfo.total});
  if(typeof refreshLayerCams==='function')refreshLayerCams();
}
function refreshAttachUI(){
  if(typeof refreshWorkspacePhotoQueue==='function')refreshWorkspacePhotoQueue();
  if(typeof syncChecklistFromEvidence==='function')syncChecklistFromEvidence();
  // The §7 per-layer counts are part of the attachment UI, so they repaint
  // here rather than only on the paths that happened to remember. They were
  // updated by the tbody input listener and by the post-upload flush, but NOT
  // by selecting a photo — so a count appeared only when you typed into the
  // next layer, which reads as a lag rather than a missing call.
  if(typeof refreshLayerCams==='function')refreshLayerCams();
  const box=document.getElementById('attach-box');
  if(!box)return;
  const archived=attachmentsEnabled();
  const pend=_pendingTotal();
  if(typeof renderAttachmentOutboxState==='function')renderAttachmentOutboxState();
  // No standing banner. A queued file is already shown as a pending chip, and
  // the archive toast reports what happened ("archiving…" → "archived · N
  // photos attached") and then goes away. A permanent line restating where
  // files live is noise: it never changes, so after the first read it is just
  // something to scroll past.
  // The §9 checklist no longer GATES the photo inputs — it reflects them. It
  // cannot do both: requiring a Yes before you may attach, while the attachment
  // is what sets the Yes, is a loop. It also silently broke the §7 camera
  // buttons, because .click() on a disabled <input type=file> does nothing at
  // all — the button looked live and simply had no effect.
  //
  // The one thing that still closes these is an explicit "No tasks done": you
  // said there were none, so the door is shut rather than left open for you to
  // walk through and then be contradicted.
  const noTasks=document.getElementById('none-task')?.checked;
  // A row marked N is the same kind of statement as "No tasks done", just
  // narrower: you have said there are no pit-wall photographs, so that door is
  // shut rather than left open for you to walk through and then be
  // contradicted. This is NOT the loop described above — the row is only forced
  // to Y by evidence, so N is always a choice you made, and un-choosing it
  // reopens the input immediately.
  const saidNo=name=>{
    const i=(typeof _rowIndex==='function')?_rowIndex(name):null;
    if(i===null)return false;
    const n=document.getElementById('yn'+i);
    return !!(n&&n.classList.contains('on'));
  };
  const closedBy={
    'att-pitwall':saidNo('Pit pictures')?'Pit pictures':'',
    'att-strat'  :saidNo('Stratigraphy pictures')?'Stratigraphy pictures':'',
  };
  ['att-sheet','att-pitwall','att-strat'].forEach(id=>{
    const inp=document.getElementById(id);
    if(!inp)return;
    const row=closedBy[id];
    inp.disabled=!!noTasks||!!row;
    inp.title=noTasks?'Untick “No tasks done” in §9 to attach photos'
      :(row?`“${row}” is marked N in §9. Change it to Y to attach photos.`:'');
  });
  // the §7 cameras read the same state
  if(typeof refreshLayerCams==='function')refreshLayerCams();
  if(archived)loadAttachList(); else renderAttachList({attachments:[],counts:{},limits:_attachInfo.limits});
}
function loadAttachList(){
  apiFetch('/api/attachments/'+encodeURIComponent(_loaded_site_id))
    .then(r=>r.json()).then(r=>{_attachInfo={counts:r.counts||{},limits:r.limits||_attachInfo.limits,
      attachments:r.attachments||[],uploads:r.uploads||[],uploadSummary:r.upload_summary||{},
      perLayer:r.per_layer||{},stratPerLayer:r.strat_per_layer||20,
      total:r.total_limit||_attachInfo.total,maxBytes:_attachInfo.maxBytes,
      byLayer:(r.attachments||[]).reduce((m,a)=>{
        if(a.category!=='stratigraphy'||a.top_cm==null)return m;
        const k=String(Math.round(a.top_cm)).padStart(3,'0')+'-'
               +String(Math.round(a.bottom_cm)).padStart(3,'0')+'cm';
        (m[k]=m[k]||[]).push(a);return m;},{}),
      expectedByLayer:(r.uploads||[]).filter(x=>x.status==='pending'&&x.category==='stratigraphy').reduce((m,a)=>{
        const k=String(Math.round(a.top_cm)).padStart(3,'0')+'-'
               +String(Math.round(a.bottom_cm)).padStart(3,'0')+'cm';
        (m[k]=m[k]||[]).push(a);return m;},{}),
      sheetPdf:(r.attachments||[]).some(a=>a.category==='sheet'&&/\.pdf$/i.test(a.filename))};renderAttachList(r);})
    .catch(()=>{});
}
// Stored + queued, across every category. Drives the §11 section indicator.
function attachCount(){
  const c=(typeof _attachInfo==='object'&&_attachInfo.counts)||{};
  const q=(typeof _pendingAttach==='object')?_pendingAttach:{};
  let n=0;
  ['sheet','pitwall','stratigraphy'].forEach(k=>{n+=(c[k]||0)+((q[k]||[]).length);});
  n+=_serverPending().length;
  return n;
}

// §11's indicator must repaint on every attachment change. tick() is what fills
// _lastPips, and attaching a photo does not run it — so the pip is refreshed
// here rather than by calling tick(), which would loop back through
// scheduleMini -> densityWarnings.
function _syncAttachPip(){
  if(typeof _lastPips!=='object'||!_lastPips)return;
  _lastPips.p11=attachCount()>0;
  if(typeof refreshStatusGlyphs==='function')refreshStatusGlyphs();
}

function renderAttachList(r){
  const el=document.getElementById('attach-list');
  if(!el)return;
  setTimeout(_syncAttachPip,0);
  const cats=[['sheet','Pit sheet'],['pitwall','Pit wall'],['stratigraphy','Stratigraphy']];
  const by={};(r.attachments||[]).forEach(a=>{(by[a.category]=by[a.category]||[]).push(a);});
  const meta=document.getElementById('att-cnt');
  const at=(r.attachments||[]).length,pd=_pendingTotal(),sp=_serverPending().length;
  if(meta)meta.textContent=(at||pd||sp)?`${at} attached${pd?` · ${pd} queued locally`:''}${sp?` · ${sp} expected on server`:''}`:'';
  const pip=document.getElementById('p11');
  if(pip)pip.classList.toggle('done',at+pd+sp>0);
  el.innerHTML=cats.map(([c,label])=>{
    const files=by[c]||[],lim=(r.limits||_attachInfo.limits)[c];
    const pend=_pendingAttach[c]||[],serverPend=_serverPendingFor(c);
    // Filenames are interpolated into innerHTML, and a QUEUED file's name comes
    // straight from the user's disk — so it is escaped, not trusted. Server
    // filenames are already sanitized but go through the same path for
    // consistency (and so an "&" in a name renders as an "&").
    // STRATIGRAPHY is listed as a count only. Every one of these photographs is
    // attached from a layer's camera in §7 and is shown — and removed while
    // still queued — in that layer's own expander, next to the interval it
    // belongs to. Repeating them here as a flat pile, divorced from their
    // layers, was the less useful of the two views.
    //
    // It also has no category cap to count against. The 20 is PER LAYER, and
    // the server never applies a per-category limit to stratigraphy at all
    // (see the `elif` in web.py): the only ceiling on the whole pit is the
    // 150-file total. Showing "n/20" claimed a limit that does not exist and
    // would have read as full at the 20th photo of a pit allowed 150.
    if(c==='stratigraphy'){
      const n=files.length+pend.length+serverPend.length;
      const note=`${_attachInfo.stratPerLayer||20} per layer · counts toward the `
                +`${_attachInfo.total||150}-file pit total`;
      return `<div class="att-row"><span class="att-cat">${label} `
        +`<span class="att-n" title="${esc(note)}">${n}</span></span>`
        +`<span class="att-none">${n?'shown per layer in §7':'none'}</span></div>`;
    }
    let chips=files.map(a=>{const f=a.filename||'';const missing=a.storage_status==='missing';
        return `<span class="att-chip${missing?' missing':''}" title="${esc(missing?(a.storage_error||'file missing'):f)}">${esc(f.replace(/^.*_(\w+_\d+\.\w+)$/,'$1'))}`
          +` <span class="att-qstate">${missing?'missing file':'stored'}</span>`
          +`<span class="att-x" role="button" tabindex="0" aria-label="Delete ${esc(f)}" title="delete attachment" onclick="deleteStoredAttachment('${esc(String(a.attachment_id))}','${esc(f)}')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();deleteStoredAttachment('${esc(String(a.attachment_id))}','${esc(f)}')}">×</span></span>`;}).join('')
      +pend.map((it,i)=>{const f=it.file||it,status=it.status||'queued';
        const label=status==='saving'?'saving locally':status==='uploading'?'uploading':
          status==='failed'?'failed — retries on Archive':status==='waiting'?'waiting to retry':status==='volatile'?'queued in memory only':'safely queued';
        const err=it.error?` · ${it.error}`:'';
        return `<span class="att-chip pending ${esc(status)}" title="${esc(label+err)}">${esc(f.name)}`
          +`${it.key?' <span class="chip-layer">'+esc(it.key)+'</span>':''}`
          +` <span class="att-qstate">${esc(label)}</span>`
          +`<span class="att-x" role="button" tabindex="0" aria-label="Remove ${esc(f.name)} from the queue" title="remove from queue" onclick="removePending('${c}',${i})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();removePending('${c}',${i})}">×</span></span>`;}).join('')
      +serverPend.map(it=>`<span class="att-chip server-pending" title="The server expects this photograph, but it is not queued in this browser.">${esc(it.filename)}`
        +` <span class="att-qstate">expected · unavailable here</span>`
        +`<span class="att-x" role="button" tabindex="0" aria-label="Cancel expected photograph ${esc(it.filename)}" title="cancel expectation" onclick="cancelServerExpected('${esc(it.queue_id)}')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();cancelServerExpected('${esc(it.queue_id)}')}">×</span></span>`).join('');
    if(!chips)chips='<span class="att-none">none</span>';
    return `<div class="att-row"><span class="att-cat">${label} <span class="att-n">${files.length+pend.length+serverPend.length}/${lim}</span></span>${chips}</div>`;
  }).join('');
}
async function cancelServerExpected(queueId){
  if(!_loaded_site_id)return;
  try{
    const response=await apiFetch('/api/attachment-queue/'+encodeURIComponent(_loaded_site_id)
      +'/'+encodeURIComponent(queueId)+'/cancel',{method:'POST'});
    const r=await response.json().catch(()=>({ok:false,msg:'cancel failed ('+response.status+')'}));
    if(!response.ok||!r.ok)throw new Error(r.msg||'Could not cancel expected photograph.');
    attachMsg('Expected photograph cancelled.','ok');loadAttachList();
  }catch(e){attachMsg('Could not cancel expected photograph: '+e.message,'err');}
}

async function deleteStoredAttachment(attachmentId,filename){
  if(!_loaded_site_id)return;
  if(!confirm(`Delete ${filename}? This removes the stored file and its attachment record.`))return;
  try{
    const response=await apiFetch('/api/attachment/'+encodeURIComponent(_loaded_site_id)
      +'/'+encodeURIComponent(attachmentId)+'/delete',{method:'POST'});
    const r=await response.json().catch(()=>({ok:false,msg:'delete failed ('+response.status+')'}));
    if(!response.ok||!r.ok)throw new Error(r.msg||'Could not delete attachment.');
    attachMsg(`${filename} deleted.`,'ok');loadAttachList();
  }catch(e){attachMsg('Could not delete attachment: '+e.message,'err');}
}

// Photographs are uploaded AS SHOT. An earlier version redrew anything over
// 2000px through a canvas and re-encoded it as JPEG q0.8 — roughly a 75% cut in
// pixels plus compression artefacts, applied silently, with the original never
// leaving the phone. That is the app making a scientific decision: a pit-wall
// overview survives it, but a crystal-card photo you would zoom into to argue
// facets versus rounds does not.
//
// Dropping it also makes deduplication exact. The sha256 was computed on the
// RE-ENCODED bytes, so the same photograph from two browsers produced two
// fingerprints and looked like two different files. Sending the original means
// the same picture is the same bytes everywhere.
//
// The 10 MB per-file limit still applies, and now applies to the real file — a
// high-resolution camera can exceed it where a downscaled copy never would.
function _downscale(file){ return Promise.resolve(file); }
async function uploadAttachment(inputId,category){
  // Selection works at any point. Files are first persisted in the browser's
  // durable outbox, then travel with Archive / Archive Changes.
  const inp=document.getElementById(inputId);
  const files=inp&&inp.files?[...inp.files]:[];
  if(!files.length)return;
  attachMsg('','');
  const lim=_attachInfo.limits[category]||6;
  const serverPending=_serverPendingFor(category);
  const have=(_attachInfo.counts[category]||0)+_pendingAttach[category].length+serverPending.length;
  if(have+files.length>lim){
    attachMsg(`Error: ${files.length} selected but only ${Math.max(0,lim-have)} ${category} slot${lim-have===1?'':'s'} remain (limit ${lim}).`,'err');
    inp.value='';return;
  }
  if(category==='sheet'){
    const isPdf=f=>f.type==='application/pdf'||/\.pdf$/i.test(f.name);
    const pdfSel=files.filter(isPdf).length,imgSel=files.length-pdfSel;
    const serverPdf=serverPending.filter(it=>it.mime_type==='application/pdf'||/\.pdf$/i.test(it.filename)).length;
    const pdfHave=(_attachInfo.sheetPdf?1:0)+_pendingAttach.sheet.filter(it=>isPdf(it.file||it)).length+serverPdf;
    const imgHave=((_attachInfo.counts.sheet||0)+_pendingAttach.sheet.length+serverPending.length)-pdfHave;
    if(pdfSel+pdfHave>1||(pdfSel+pdfHave>0&&imgSel+imgHave>0)){
      attachMsg('Error: the pit sheet is one PDF OR up to three images. Remove one or the other.','err');
      inp.value='';return;
    }
  }
  let meta={};
  if(category==='stratigraphy'){
    const top=num(inp.dataset.top),bottom=num(inp.dataset.bottom);
    const key=(top!==null&&bottom!==null)
      ?String(Math.round(top)).padStart(3,'0')+'-'+String(Math.round(bottom)).padStart(3,'0')+'cm':'';
    meta={top,bottom,key};delete inp.dataset.top;delete inp.dataset.bottom;
  }
  inp.value='';
  attachMsg(`saving ${files.length} photograph${files.length===1?'':'s'} locally…`,'');
  const result=await queueAttachmentFiles(category,files,meta);
  const bits=[];
  if(result.added.length)bits.push(`${result.added.length} safely queued`);
  if(result.duplicates.length)bits.push(`${result.duplicates.length} duplicate selection${result.duplicates.length===1?'':'s'} skipped`);
  if(result.failed.length)bits.push(`${result.failed.length} could not be queued`);
  const detail=result.failed.length?'\n'+result.failed.map(x=>`· ${x.name}: ${x.msg}`).join('\n'):'';
  attachMsg((bits.join(' · ')||'No files queued')+detail,result.failed.length?'err':'ok');
  refreshAttachUI();
}

let _attachmentRetryTimer=null;
function _attachmentRetrySeconds(response){
  const raw=response&&response.headers&&response.headers.get
    ?response.headers.get('Retry-After'):null;
  if(raw){
    const seconds=Number(raw);
    if(Number.isFinite(seconds)&&seconds>=0)return Math.max(1,Math.min(300,Math.ceil(seconds)));
    const when=Date.parse(raw);
    if(Number.isFinite(when))return Math.max(1,Math.min(300,Math.ceil((when-Date.now())/1000)));
  }
  return 5;
}
function _scheduleAttachmentRetry(seconds){
  clearTimeout(_attachmentRetryTimer);
  const wait=Math.max(1,Number(seconds)||5);
  _attachmentRetryTimer=setTimeout(async()=>{
    if(_attachmentUploadBusy||!attachmentsEnabled()){_scheduleAttachmentRetry(2);return;}
    try{await flushPendingAttachments();}
    catch(err){attachMsg('Photographs remain safely queued: '+fetchErr(err),'err');}
  },wait*1000);
}

async function flushPendingAttachments(){
  clearTimeout(_attachmentRetryTimer);
  _attachmentRetryTimer=null;
  // Each durable outbox item is attempted independently. A failure remains in
  // IndexedDB with status=failed; successful and byte-identical duplicate
  // confirmations are removed from both IndexedDB and the visible queue.
  await awaitAttachmentQueueReady();
  if(!attachmentsEnabled())return{done:0,failed:0,queued:_pendingTotal(),rejected:[],duplicates:[],deferred:[],by:{}};
  let done=0,netErr=null,throttled=null;
  const rejected=[],duplicates=[],deferred=[],by={};
  const bucket=c=>(by[c]=by[c]||{done:0,dups:0,rejected:0});
  const total=_pendingTotal();let attempted=0;
  _attachmentUploadBusy=true;
  try{
    for(const category of ['sheet','pitwall','stratigraphy']){
      const b=bucket(category);
      const items=[..._pendingAttach[category]];
      for(const item of items){
        const file=item.file||item;attempted++;
        item.status='uploading';item.error='';
        await updateQueuedAttachment(item,category).catch(()=>{});refreshAttachUI();
        attachMsg(`uploading ${attempted}/${total}…`,'');
        let blob;
        try{blob=(file.type==='application/pdf')?file:await _downscale(file);}catch(e){blob=file;}
        const fd=new FormData();
        fd.append('category',category);
        fd.append('queue_id',item.queue_id||'');
        if(item.top!=null)fd.append('top_cm',item.top);
        if(item.bottom!=null)fd.append('bottom_cm',item.bottom);
        fd.append('file',blob,file.name);
        try{
          const response=await apiFetch('/api/attach/'+encodeURIComponent(_loaded_site_id),{method:'POST',body:fd});
          const r=await response.json().catch(()=>({ok:false,msg:`upload failed (${response.status})`}));
          if(response.status===429){
            const retryAfter=_attachmentRetrySeconds(response);
            item.status='waiting';
            item.error=`Server is pacing uploads; retrying automatically in ${retryAfter}s`;
            deferred.push({name:file.name,msg:item.error,category,retry_after:retryAfter});
            await updateQueuedAttachment(item,category).catch(()=>{});
            throttled={retryAfter,name:file.name};
            _scheduleAttachmentRetry(retryAfter);
            break;
          }
          if(!response.ok||!r.ok){
            item.status='failed';item.error=r.msg||`upload failed (${response.status})`;
            rejected.push({name:file.name,msg:item.error,category});b.rejected++;
            await updateQueuedAttachment(item,category).catch(()=>{});continue;
          }
          if(r.duplicate){duplicates.push({name:file.name,category,layer:r.layer||item.key||''});b.dups++;}
          else{done++;b.done++;}
          await confirmQueuedAttachment(item,category);
        }catch(err){
          item.status='failed';item.error=fetchErr(err);netErr=item.error;
          await updateQueuedAttachment(item,category).catch(()=>{});
          break;
        }
      }
      if(netErr||throttled)break;
    }
  }finally{_attachmentUploadBusy=false;}
  loadAttachList();refreshAttachUI();refreshLayerCams();
  const LABEL={sheet:'Pit sheet',pitwall:'Pit wall',stratigraphy:'Stratigraphy'};
  const lines=[];
  for(const c of ['sheet','pitwall','stratigraphy']){
    const b=by[c];if(!b||(!b.done&&!b.dups&&!b.rejected))continue;
    const bits=[b.done+' uploaded'];
    if(b.dups){
      const where=[...new Set(duplicates.filter(x=>x.category===c&&x.layer).map(x=>x.layer))];
      bits.push(b.dups+' skipped (already on '+(where.length?where.join(', '):'this pit')+')');
    }
    if(b.rejected)bits.push(b.rejected+' failed and retained');
    lines.push(LABEL[c]+': '+bits.join(', '));
  }
  if(netErr)attachMsg('Error: '+netErr+'. Files remain safely queued — press Archive again to retry.','err');
  else if(throttled)attachMsg(`Upload pace limit reached. Files remain safely queued; retrying automatically in ${throttled.retryAfter}s.`,'');
  else if(lines.length){
    const detail=rejected.length?'\n'+rejected.map(x=>`· ${x.name}: ${x.msg}`).join('\n'):'';
    attachMsg(lines.join('\n')+detail,rejected.length?'err':'ok');
  }
  const remaining=[..._pendingAttach.sheet,..._pendingAttach.pitwall,..._pendingAttach.stratigraphy];
  const failed=remaining.filter(x=>x.status==='failed').length;
  const queued=remaining.length-failed;
  return{done,failed,queued,rejected,duplicates,deferred,by,err:netErr,throttled:!!throttled};
}

window.addEventListener('beforeunload',e=>{
  const all=[..._pendingAttach.sheet,..._pendingAttach.pitwall,..._pendingAttach.stratigraphy];
  if(all.some(x=>x.status==='saving'||x.status==='volatile')){e.preventDefault();e.returnValue='';}
});

// ---------------------------------------------------------------------------
// PER-LAYER STRATIGRAPHY PHOTOS
//
// The photo is attached from the layer's own row in §7, because that is where
// the association is obvious: you are looking at the layer, you photograph it,
// you attach it on that line. The alternative — a layer dropdown on each chip
// in §11 — asks you to recall half an hour later which of four thumbnails was
// the depth hoar.
//
// What travels with the file is the layer's DEPTH INTERVAL, never a layer id.
// Layers are deleted and rebuilt on every archive, so their ids are reassigned;
// a depth is a fact about the snowpack and survives that, and survives a layer
// being split in two.
// ---------------------------------------------------------------------------
function _layerBounds(tr){
  const ins=tr.querySelectorAll('input[type=number]');
  const top=num(ins[0]?.value), bot=num(ins[1]?.value);
  return (top===null||bot===null||top<=bot)?null:{top,bottom:bot};
}
function _layerKey(b){
  return b ? String(Math.round(b.top)).padStart(3,'0')+'-'
           + String(Math.round(b.bottom)).padStart(3,'0')+'cm' : '';
}

// Enable a row's camera only once BOTH depths are present and sane — the
// interval IS the link, so there is nothing to attach to without it.
function refreshLayerCams(){
  const perLayer=_attachInfo.perLayer||{};
  const cap=_attachInfo.stratPerLayer||20;
  const noTasks=document.getElementById('none-task')?.checked;
  // "Stratigraphy pictures" marked N in §9 closes the layer cameras too, the
  // same way it closes the §11 input — one statement, one consequence.
  const strNo=(()=>{
    const i=(typeof _rowIndex==='function')?_rowIndex('Stratigraphy pictures'):null;
    if(i===null)return false;
    const n=document.getElementById('yn'+i);
    return !!(n&&n.classList.contains('on'));
  })();
  const blocked=noTasks||strNo;
  document.querySelectorAll('#sb tr').forEach(tr=>{
    const btn=tr.querySelector('.cam'); if(!btn)return;
    const b=_layerBounds(tr), key=_layerKey(b);
    const queued=_pendingAttach.stratigraphy.filter(x=>x.key===key).length;
    const expected=_serverPendingFor('stratigraphy').filter(x=>
      String(Math.round(x.top_cm)).padStart(3,'0')+'-'+String(Math.round(x.bottom_cm)).padStart(3,'0')+'cm'===key).length;
    const n=(perLayer[key]||0)+queued+expected;
    btn.querySelector('.cam-n').textContent=n||'';
    btn.classList.toggle('has',n>0);
    const exp=tr.querySelector('.lp-toggle');
    if(exp){
      exp.style.display=n>0?'':'none';
      exp.title=`Show this layer's ${n} photograph${n>1?'s':''}`;
    }
    btn.disabled=!b||blocked||n>=cap;
    const why=!b   ? 'Enter this layer\u2019s top and bottom first \u2014 the photo is filed against the interval, so there is nothing to attach it to yet'
      : noTasks    ? 'Untick \u201cNo tasks done\u201d in \u00a79 to attach photos'
      : strNo      ? '\u201cStratigraphy pictures\u201d is marked N in \u00a79. Change it to Y to attach photos.'
      : n>=cap     ? `This layer already has ${cap} photos`
      : `Attach photos of ${key} (${n}/${cap})`;
    btn.title=why;
    // A title on a DISABLED control is inert: Chrome, Firefox and Safari all
    // suppress the tooltip, so the one state that most needs explaining — a
    // greyed-out camera on a half-filled row — was the one state that could not
    // explain itself. The cell is never disabled, so it can carry the same text
    // and the hover works. (This is also why §7's instruction line used to spell
    // the rule out in prose.)
    const cell=btn.closest('td');
    if(cell)cell.title=why;
  });
}

function pickLayerPhotos(btn){
  const tr=btn.closest('tr'), b=_layerBounds(tr);
  if(!b)return;
  const inp=document.getElementById('att-strat');
  if(!inp)return;
  // one hidden file input, retargeted at whichever layer was clicked
  inp.dataset.top=b.top; inp.dataset.bottom=b.bottom;
  inp.click();
}

// ---------------------------------------------------------------------------
// LAYER PHOTO EXPANDER
//
// Attaching a photo and never seeing what you attached is the gap: the badge
// says "3" and the only way to check was §11, grouped by category rather than
// by layer. Clicking the badge opens a thin row DIRECTLY BENEATH that layer
// listing its photos, each removable while still queued.
//
// A row rather than a column: vertical space is cheap in a form that already
// runs to 14 columns, and the photos read as belonging to the layer above them.
// Filenames, not thumbnails — decoding twenty full-resolution JPEGs is real
// work on a field laptop, and this is a form about numbers.
// ---------------------------------------------------------------------------
function toggleLayerPhotos(btn){
  const tr=btn.closest('tr'); if(!tr)return;
  const open=tr.nextElementSibling&&tr.nextElementSibling.classList.contains('lp-row');
  document.querySelectorAll('#sb .lp-row').forEach(r=>r.remove());
  if(open){refreshLayerCams();return;}          // clicking again closes it
  const b=_layerBounds(tr); if(!b)return;
  const key=_layerKey(b);
  const queued=_pendingAttach.stratigraphy
    .map((it,i)=>({it,i})).filter(x=>x.it.key===key);
  const stored=(_attachInfo.byLayer&&_attachInfo.byLayer[key])||[];
  const localIds=_localQueueIds();
  const expected=((_attachInfo.expectedByLayer&&_attachInfo.expectedByLayer[key])||[]).filter(x=>!localIds.has(x.queue_id));
  const cols=tr.children.length;
  const row=document.createElement('tr');
  row.className='lp-row';
  const chips=[
    ...stored.map(a=>{const f=a.filename||'';const missing=a.storage_status==='missing';return `<span class="att-chip${missing?' missing':''}" title="${esc(missing?(a.storage_error||'file missing'):f)}">${esc(f.replace(/^.*_(\w+_\d+\.\w+)$/,'$1'))}`
      +` <span class="att-qstate">${missing?'missing file':'stored'}</span>`
      +`<span class="att-x" role="button" tabindex="0" aria-label="Delete ${esc(f)}" onclick="deleteStoredAttachment('${esc(String(a.attachment_id))}','${esc(f)}')">×</span></span>`;}),
    ...expected.map(it=>`<span class="att-chip server-pending" title="Expected on the server but unavailable in this browser">${esc(it.filename)} <span class="att-qstate">expected · unavailable here</span>`
      +`<span class="att-x" role="button" tabindex="0" aria-label="Cancel expected photograph ${esc(it.filename)}" onclick="cancelServerExpected('${esc(it.queue_id)}')">×</span></span>`),
    ...queued.map(({it,i})=>{const status=it.status||'queued';
      const label=status==='saving'?'saving locally':status==='uploading'?'uploading':status==='failed'?'failed — retry on Archive':status==='waiting'?'waiting to retry':'safely queued';
      return `<span class="att-chip pending ${esc(status)}" title="${esc(label+(it.error?' · '+it.error:''))}">${esc(it.file.name)}`
        +` <span class="att-qstate">${esc(label)}</span>`
        +`<span class="att-x" role="button" tabindex="0" aria-label="Remove ${esc(it.file.name)} from the queue"`
        +` onclick="removeLayerPhoto(${i})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();removeLayerPhoto(${i})}">×</span></span>`;})
  ];
  row.innerHTML=`<td colspan="${cols}"><div class="lp-wrap">`
    +`<span class="lp-lbl">${esc(key)}</span>`
    +(chips.length?chips.join(''):'<span class="lp-empty">no photographs yet</span>')
    +`</div></td>`;
  tr.after(row);
}
function removeLayerPhoto(i){
  // Capture the host row BEFORE anything repaints, then rebuild the expander
  // in place. refreshAttachUI() removes the open row, and toggleLayerPhotos()
  // treats an existing row as "close me", so reopening has to happen after the
  // repaint and with the row already gone — otherwise removing a chip silently
  // collapsed the list you were working in.
  const openRow=document.querySelector('#sb .lp-row');
  const host=openRow&&openRow.previousElementSibling;
  const item=_pendingAttach.stratigraphy[i];
  if(item)removeQueuedAttachment(item,'stratigraphy').then(()=>{
    document.querySelectorAll('#sb .lp-row').forEach(r=>r.remove());
    const btn=host&&host.querySelector('.lp-toggle');
    if(btn&&btn.style.display!=='none')toggleLayerPhotos(btn);
  });
}
