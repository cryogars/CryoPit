/* Lightweight unit tests load modules independently; the assembled app gets
   the CSRF-aware implementation from 00_core.js. */
if(typeof apiFetch==='undefined'){var apiFetch=(path,options)=>fetch((typeof API==='undefined'?'':API)+path,options);}

function validate(){
  const p=collect();const e=[];
  if(!p.meta.location)e.push('Location');
  if(!p.meta.site)e.push('Site');
  if(!p.meta.pit_id||p.meta.pit_id==='—')e.push('Pit ID');
  if(!p.meta.recorded_by)e.push('Recorded by');
  if(!p.meta.surveyors)e.push('Field observers');
  if(!p.meta.date)e.push('Date');
  // HHMM times block save when malformed instead of just tinting red
  // The blocker list IS global — it appears as one toast covering the whole
  // form — so unlike the in-section warnings these keep their section number.
  TIME_FIELDS.forEach(([id,lbl,sec])=>{
    const v=gv(id);
    if(v&&!goodTime(v))e.push(`${lbl} (${sec}, HHMM)`);
  });
  _invertedIntervals().forEach(x=>e.push(x));
  _physicalBounds().forEach(x=>e.push(x));
  // Calibration data must name its device — the DB never guesses one.
  if((gv('ssa-spec').trim()||gv('ssa-calv').trim())&&!gv('ssa-inst'))
    e.push('SSA instrument (§8)');
  return{p,e};
}

// Transient messages are TOASTS (bottom-right, self-dismissing: 6 s for
// info/success, 12 s for errors, hover pauses). The topbar chip (#tb-st)
// keeps only the pit's persistent STATE — see setchip().
function setst(msg,cls,opts){toast(msg,cls,opts);}
function setchip(msg,cls){const el=document.getElementById('tb-st');el.textContent=msg;el.className='tb-status'+(cls?' '+cls:' unsaved');}
function toast(msg,kind,opts={}){
  let wrap=document.getElementById('toasts');
  if(!wrap){
    wrap=document.createElement('div');wrap.id='toasts';
    // Toasts carry EVERY transient result — "archived", "Download blocked: …",
    // upload failures. Without a live region a screen-reader user got no
    // feedback at all from Archive or Download.
    wrap.setAttribute('role','status');
    wrap.setAttribute('aria-live','polite');
    wrap.setAttribute('aria-atomic','false');
    document.body.appendChild(wrap);
  }
  // errors interrupt; successes wait for a pause
  wrap.setAttribute('aria-live',kind==='err'?'assertive':'polite');
  let t=opts.id?wrap.querySelector('[data-tid="'+opts.id+'"]'):null;
  if(!t){t=document.createElement('div');if(opts.id)t.dataset.tid=opts.id;wrap.appendChild(t);}
  t.className='toast '+(kind==='ok'?'ok':kind==='err'?'err':'info');
  t.textContent=msg;
  const ms=kind==='err'?12000:6000;
  const arm=()=>{t._tm=setTimeout(()=>{t.classList.add('bye');setTimeout(()=>t.remove(),350);},ms);};
  clearTimeout(t._tm);arm();
  t.onmouseenter=()=>clearTimeout(t._tm);
  t.onmouseleave=arm;
}

// Per-endpoint timeouts. A flat 15 s covered every call, but the three heavy
// endpoints do real work: /api/archive writes the DB, builds seven CSVs AND
// renders the matplotlib figure (whose first call also pays the import cost),
// /api/download builds the same files plus a zip, and /api/profile renders.
// On a cold, cheap field laptop with a 60-layer pit those can exceed 15 s — and
// the abort fired CLIENT-side, so the request kept running on the server. The
// pit archived fine; the user was told "no response — is the app running?" and
// would reasonably archive again.
const TIMEOUTS={'/api/archive':120000,'/api/download':120000,'/api/profile':60000};
function post(path,payload,timeoutMs){
  const ms=timeoutMs||TIMEOUTS[path]||15000;
  const ctrl=new AbortController();
  const tid=setTimeout(()=>ctrl.abort(),ms);
  return apiFetch(path,{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(payload),signal:ctrl.signal
  }).finally(()=>clearTimeout(tid));
}
function fetchErr(err,opts={}){
  if(err.name!=='AbortError')return 'error: '+err.message;
  // An abort means WE stopped waiting — not that the server stopped working.
  // For a write endpoint that distinction decides whether re-clicking Archive
  // is safe, so never imply the work didn't happen.
  return opts.write
    ? 'timed out waiting for the server. The pit MAY still have been archived — '
      + 'check Saved pits before archiving again.'
    : 'no response — is the app running?';
}

// Draft autosave: the form state survives refreshes/crashes ----------
let _draftT=null;
function scheduleDraft(){
  if(_restoring)return;
  clearTimeout(_draftT);
  _draftT=setTimeout(()=>{
    try{localStorage.setItem('cp-draft',JSON.stringify(collect()));}catch(e){}
  },500);
}
function clearDraft(){
  clearTimeout(_draftT);   // cancel any pending write so it can't resurrect the draft
  try{localStorage.removeItem('cp-draft');}catch(e){}
}
function restoreDraft(){
  try{
    const d=localStorage.getItem('cp-draft');
    if(!d)return;
    const p=JSON.parse(d);
    if(!p||!p.meta)return;
    const meaningful=(p.meta.pit_id&&p.meta.pit_id!=='—')||p.meta.recorded_by||
      (p.temperature&&p.temperature.length)||(p.stratigraphy&&p.stratigraphy.length);
    if(!meaningful)return;
    populate(p);
    if(p.site_id)setRecordMode(p.site_id,p.meta.pit_id,false);
    if(typeof refreshWorkspaceCurrent==='function')refreshWorkspaceCurrent();
    setst('● draft restored — not saved','unsaved');
  }catch(e){}
}
let _archiveBusy=false;
function setRecordMode(siteId,pitId,pending=false,refreshAttachments=true){
  _loaded_site_id=siteId||null;
  _loaded_pit_id=pitId||null;
  const banner=document.getElementById('record-mode');
  const title=document.getElementById('record-mode-title');
  const detail=document.getElementById('record-mode-detail');
  const btn=document.getElementById('archive-btn');
  if(_loaded_site_id){
    if(banner)banner.hidden=false;
    if(title)title.textContent=pending?'Archive needs recovery':'Editing archived pit';
    if(detail)detail.textContent=(pitId||'')+(pending?' · retry Archive to recover':' · archiving updates this existing record');
    if(btn)btn.textContent=pending?'Retry Archive':'Archive Changes';
  }else{
    if(banner)banner.hidden=true;
    if(btn)btn.textContent='Archive';
  }
  if(refreshAttachments&&typeof refreshAttachUI==='function')refreshAttachUI();
  if(typeof refreshWorkspaceCurrent==='function')refreshWorkspaceCurrent();
}
function dismissPostArchive(){const el=document.getElementById('post-archive');if(el)el.hidden=true;}
async function newPit(){
  if(_archiveBusy||((typeof attachmentQueueIsBusy==='function')&&attachmentQueueIsBusy())){
    setst('Please wait for the current archive or photo upload to finish.','err');return;
  }
  const queued=(typeof _pendingTotal==='function')?_pendingTotal():0;
  const bits=[];
  if(_loaded_site_id)bits.push('The archived pit will remain saved.');
  else bits.push('This form has not been archived.');
  if(queued)bits.push(`${queued} locally queued photograph${queued===1?'':'s'} will be permanently discarded from this browser.`);
  bits.push('CryoPit does not use saved pits as templates; the new form starts clean.');
  if(!confirm('Start a new pit?\n\n'+bits.join('\n')))return;
  try{
    if(typeof discardAttachmentQueue==='function')await discardAttachmentQueue({rotateDraft:true});
    clearDraft();
    location.reload();
  }catch(e){setst('Could not clear the local photo queue: '+e.message,'err');}
}

// Download: pure file delivery. Exports the CSVs to the user's browser and
// touches nothing server-side — no database write. A team that only wants CSVs
// is never forced into the database.
function doDownload(){
  const{p,e}=validate();
  if(e.length){setst('Download blocked: '+e.join('; '),'err');return;}
  setst('exporting…','');
  post('/api/download',p)
    .then(async r=>{
      // Success is a zip; failure is JSON. Distinguish by Content-Type rather
      // than by trying to parse a body that may be hundreds of megabytes.
      const ct=r.headers.get('Content-Type')||'';
      if(!r.ok||ct.includes('application/json')){
        const j=await r.json().catch(()=>({msg:'download failed ('+r.status+')'}));
        setst('error: '+(j.msg||'download failed'),'err');
        return;
      }
      const zipname=r.headers.get('X-CryoPit-Zipname')||'cryopit.zip';
      downloadZip(zipname,await r.blob());
      // NOTE: downloading does NOT change archived state — files ≠ recorded.
      setst('● downloaded (not archived) · '+zipname,'ok-dl');
    })
    .catch(err=>setst(fetchErr(err),'err'));
}

// Archive: create a new record or update the immutable site_id currently
// loaded in edit mode. There is no overwrite prompt and no templating path.
async function doArchive(){
  if(_archiveBusy)return;
  try{
    if(typeof awaitAttachmentQueueReady==='function')await awaitAttachmentQueueReady();
  }catch(e){setst('Archive blocked: photographs are not safely queued ('+e.message+').','err');return;}
  const{p,e}=validate();
  if(e.length){setst('Archive blocked: '+e.join('; '),'err');return;}
  if(typeof attachmentUploadManifest==='function')p.attachment_manifest=attachmentUploadManifest();
  _archive(p);
}
function _archive(p){
  _archiveBusy=true;
  const btn=document.getElementById('archive-btn');if(btn)btn.disabled=true;
  setst(_loaded_site_id?'archiving changes…':'archiving…','');
  post('/api/archive',p)
    .then(async response=>{
      const r=await response.json().catch(()=>({ok:false,msg:'archive failed ('+response.status+')'}));
      if(r.exists){
        setst('● not archived — that Pit ID already exists. Load it from Saved pits to edit it.','err');
        return;
      }
      if(!r.ok){
        if(r.pending&&r.site_id){
          setRecordMode(r.site_id,r.pit_id||p.meta.pit_id,true);
          if(typeof bindAttachmentQueueToSite==='function')await bindAttachmentQueueToSite(r.site_id);
          scheduleDraft();
          loadSavedPits();
          if(typeof loadWorkspaceSummary==='function')loadWorkspaceSummary();
          setchip('● archive needs recovery · '+(r.pit_id||p.meta.pit_id),'err');
          setst('Archive interrupted: '+r.msg+' Retry Archive or use Needs recovery.','err');
        }else setst('● error: '+r.msg,'err');
        return;
      }
      setRecordMode(r.site_id,r.pit_id,false);
      if(typeof bindAttachmentQueueToSite==='function')await bindAttachmentQueueToSite(r.site_id);
      clearDraft();
      loadSavedPits();
      if(typeof loadWorkspaceSummary==='function')loadWorkspaceSummary();
      setchip('● archived · '+r.pit_id,'ok');
      const lines=[r.updated?'Changes archived':'Pit archived'];
      if(r.recovered)lines[0]+=' (recovered interrupted operation)';
      if(r.folder)lines.push('Export: '+shortPath(r.folder));
      if(r.csv_count!=null){
        const figs=[r.has_png&&'PNG',r.has_pdf&&'PDF'].filter(Boolean).join(' + ');
        lines.push('CSVs: '+r.csv_count+(figs?' · profile figure: '+figs:''));
      }
      if(!r.updated){const post=document.getElementById('post-archive');if(post)post.hidden=false;}
      else dismissPostArchive();
      const registered=(r.photo_uploads&&r.photo_uploads.pending)||0;
      const localManifest=(p.attachment_manifest||[]).length;
      const unavailable=Math.max(0,registered-localManifest);
      if(unavailable)lines.push(`Photos: ${unavailable} expected on the server but unavailable in this browser`);
      const a=await flushPendingAttachments();
      const dup=(a.duplicates||[]).length, rej=(a.rejected||[]).length;
      if(a.done||dup||rej||a.failed||a.queued){
        const bits=[a.done+' attached'];
        if(dup)bits.push(dup+' skipped (duplicate)');
        if(rej)bits.push(rej+' failed');
        if(a.failed)bits.push(a.failed+' failed and retained');
        if(a.queued)bits.push(a.queued+(a.throttled?' waiting for automatic retry':' still queued'));
        lines.push('Photos: '+bits.join('; '));
      }
      toast(lines.join('\n'),(a.failed||rej)?'err':'ok',{id:'archive'});
    })
    .catch(err=>{
      setst(fetchErr(err,{write:true}),'err');
      loadSavedPits();
    })
    .finally(()=>{_archiveBusy=false;if(btn)btn.disabled=false;});
}

function downloadZip(zipname,blob){
  // The response IS the zip. Previously this took a base64 string, ran atob()
  // over it and copied the result one byte at a time into a Uint8Array — three
  // full copies of the archive in memory before the download even began.
  const url=URL.createObjectURL(blob);
  const a=document.createElement('a');
  a.href=url;a.download=zipname;
  document.body.appendChild(a);a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function shortPath(pth){
  if(!pth)return'';
  const parts=pth.split(/[\\/]/).filter(Boolean);
  return parts.length<=2?pth:'…/'+parts.slice(-2).join('/');
}

// Saved Pits finder + loading -----------------------------------------
let _savedPitsOffset=0;
let _savedPitsShown=0;
let _savedPitsLoading=false;
let _savedPitsReloadQueued=false;
let _savedPitsSearchTimer=null;

function savedPitsQuery(offset=0){
  const qs=new URLSearchParams();
  qs.set('offset',String(Math.max(0,offset||0)));
  const search=document.getElementById('saved-pits-search');
  const campaign=document.getElementById('saved-pits-campaign');
  const from=document.getElementById('saved-pits-date-from');
  const to=document.getElementById('saved-pits-date-to');
  const sort=document.getElementById('saved-pits-sort');
  if(search&&search.value.trim())qs.set('q',search.value.trim());
  if(campaign&&campaign.value)qs.set('campaign',campaign.value);
  if(from&&from.value)qs.set('date_from',from.value);
  if(to&&to.value)qs.set('date_to',to.value);
  if(sort&&sort.value&&sort.value!=='date')qs.set('sort',sort.value);
  return qs.toString();
}

function _savedPitLine(className,text){
  const span=document.createElement('span');span.className=className;span.textContent=text;return span;
}

function _renderSavedPit(p){
  const button=document.createElement('button');
  button.type='button';button.className='pit-entry';button.title='Load '+p.pit_id;
  button.appendChild(_savedPitLine('pit-id',p.pit_id||'Unnamed pit'));

  // The sidebar is fast access, not a second workspace. Keep ordinary rows to
  // two quiet lines: identity first, then the most useful field context.
  // Campaign remains searchable/filterable and is only shown as a fallback
  // when site/location context is unavailable.
  const place=[p.site,p.location].filter(Boolean).join(' · ');
  const meta=[];
  if(place)meta.push(place);
  else if(p.campaign)meta.push(p.campaign);
  if(p.date)meta.push(p.date);
  if(meta.length)button.appendChild(_savedPitLine('pit-meta',meta.join(' · ')));

  // Saved pits are archived by definition, so only show states that require
  // attention. Plain alert text is easier to scan in a 206 px sidebar than a
  // row of status pills.
  if(p.pending_photos||p.missing_attachments){
    const alerts=document.createElement('span');alerts.className='pit-alerts';
    if(p.pending_photos)alerts.appendChild(_savedPitLine('pit-alert pending',`${p.pending_photos} photo${p.pending_photos===1?'':'s'} pending`));
    if(p.missing_attachments)alerts.appendChild(_savedPitLine('pit-alert missing',`${p.missing_attachments} missing`));
    button.appendChild(alerts);
  }
  button.addEventListener('click',()=>loadPit(p.site_id,p.pit_id));
  return button;
}

function _renderRecoveryPits(pending){
  const box=document.getElementById('recovery-pits');
  if(!box)return;
  box.innerHTML='';
  if(!pending.length){box.hidden=true;return;}
  box.hidden=false;
  const head=document.createElement('div');head.className='recovery-heading';
  head.textContent=`Needs recovery · ${pending.length}`;box.appendChild(head);
  pending.forEach(p=>{
    const row=document.createElement('div');row.className='pending-entry';
    const text=document.createElement('span');text.className='pending-entry-text';
    text.appendChild(_savedPitLine('pit-id',p.pit_id||'Unnamed pit'));
    if(p.updated_at)text.appendChild(_savedPitLine('pit-date',`updated ${String(p.updated_at).slice(0,10)}`));
    row.appendChild(text);
    const b=document.createElement('button');b.type='button';b.textContent='Recover';
    b.addEventListener('click',()=>recoverPit(p.site_id,p.pit_id,b));row.appendChild(b);
    box.appendChild(row);
  });
}

function _updateSavedPitCampaigns(campaigns){
  const select=document.getElementById('saved-pits-campaign');
  if(!select)return;
  const selected=select.value;
  select.innerHTML='';
  const all=document.createElement('option');all.value='';all.textContent='All campaigns';select.appendChild(all);
  (campaigns||[]).forEach(c=>{
    const option=document.createElement('option');option.value=c.name;
    option.textContent=`${c.name} (${c.count})`;select.appendChild(option);
  });
  if(Array.from(select.options).some(o=>o.value===selected))select.value=selected;
}

function loadSavedPits(opts={}){
  if(!ENABLE_EDIT)return Promise.resolve();
  const list=document.getElementById('saved-pits-list');
  if(!list)return Promise.resolve();
  const append=!!opts.append;
  const from=document.getElementById('saved-pits-date-from');
  const to=document.getElementById('saved-pits-date-to');
  if(from&&to&&from.value&&to.value&&from.value>to.value){
    setst('Saved pits: From date must not be later than To date.','err');
    return Promise.resolve();
  }
  if(_savedPitsLoading){_savedPitsReloadQueued=true;return Promise.resolve();}
  _savedPitsLoading=true;
  if(!append){_savedPitsOffset=0;_savedPitsShown=0;}
  list.setAttribute('aria-busy','true');
  const more=document.getElementById('saved-pits-more');if(more)more.disabled=true;
  return apiFetch('/api/pits?'+savedPitsQuery(append?_savedPitsOffset:0))
    .then(r=>{if(!r.ok)throw new Error('saved pits request failed ('+r.status+')');return r.json();})
    .then(r=>{
      const pits=r.pits||[], pending=r.pending||[];
      if(!append)list.innerHTML='';
      pits.forEach(p=>list.appendChild(_renderSavedPit(p)));
      _savedPitsOffset=(r.offset||0)+pits.length;
      _savedPitsShown=append?_savedPitsShown+pits.length:pits.length;
      if(!_savedPitsShown){
        const empty=document.createElement('span');empty.className='nav-foot-empty';
        empty.textContent=(savedPitsQuery(0).replace('offset=0','').replace(/^&|&$/g,''))?'No matching pits':'none yet';
        list.appendChild(empty);
      }
      const count=document.getElementById('saved-pits-count');
      if(count)count.textContent=r.total?`${_savedPitsShown} of ${r.total}`:'0';
      if(more){more.hidden=!r.has_more;more.disabled=false;}
      _renderRecoveryPits(pending);
      _updateSavedPitCampaigns(r.campaigns||[]);
    })
    .catch(err=>{
      if(!append){
        list.innerHTML='';
        const empty=document.createElement('span');empty.className='nav-foot-empty';
        empty.textContent='Could not load saved pits';list.appendChild(empty);
        if(more)more.hidden=true;
      }
      setst('Saved pits: '+err.message,'err');
    })
    .finally(()=>{
      list.setAttribute('aria-busy','false');_savedPitsLoading=false;
      if(more)more.disabled=false;
      if(_savedPitsReloadQueued){_savedPitsReloadQueued=false;loadSavedPits();}
    });
}

function initSavedPitsFinder(){
  if(!ENABLE_EDIT)return;
  const form=document.getElementById('saved-pits-filters');
  const search=document.getElementById('saved-pits-search');
  const campaign=document.getElementById('saved-pits-campaign');
  const from=document.getElementById('saved-pits-date-from');
  const to=document.getElementById('saved-pits-date-to');
  const sort=document.getElementById('saved-pits-sort');
  const more=document.getElementById('saved-pits-more');
  if(search)search.addEventListener('input',()=>{
    clearTimeout(_savedPitsSearchTimer);
    _savedPitsSearchTimer=setTimeout(()=>loadSavedPits(),250);
  });
  [campaign,from,to,sort].filter(Boolean).forEach(el=>el.addEventListener('change',()=>loadSavedPits()));
  if(form)form.addEventListener('submit',e=>{e.preventDefault();loadSavedPits();});
  if(form)form.addEventListener('reset',()=>setTimeout(()=>loadSavedPits(),0));
  if(more)more.addEventListener('click',()=>loadSavedPits({append:true}));
}

function recoverPit(siteId,pitId,button){
  if(button)button.disabled=true;
  return apiFetch('/api/recover/'+encodeURIComponent(siteId),{method:'POST'})
    .then(r=>r.json()).then(r=>{
      if(!r.ok){setst('Recovery failed: '+r.msg,'err');return r;}
      setst('Recovered '+pitId,'ok');loadSavedPits();
      if(typeof loadWorkspaceSummary==='function')loadWorkspaceSummary();
      return r;
    }).catch(err=>{setst(fetchErr(err,{write:true}),'err');return{ok:false};})
    .finally(()=>{if(button)button.disabled=false;});
}
function formDirty(){
  const pid=document.getElementById('pitid').textContent.trim();
  return !!((pid&&pid!=='—')||gv('recby').trim()||
    document.getElementById('tb').children.length>0||
    document.getElementById('sb').children.length>0||
    ((typeof _pendingTotal==='function')&&_pendingTotal()>0));
}
async function loadPit(siteId,pitId){
  const queued=(typeof _pendingTotal==='function')?_pendingTotal():0;
  const switching=_loaded_site_id!==siteId;
  if(switching&&queued&&_loaded_site_id&&typeof attachmentOutboxIsAvailable==='function'&&!attachmentOutboxIsAvailable()){
    setst('Cannot switch pits while photographs are queued only in memory. Archive them or remove them first.','err');
    return;
  }
  if(formDirty()&&switching){
    const notes=[];
    if(queued&&!_loaded_site_id)notes.push(`${queued} queued photograph${queued===1?'':'s'} for this unarchived form will be discarded.`);
    else if(queued&&_loaded_site_id)notes.push(`${queued} queued photograph${queued===1?'':'s'} will remain associated with the pit currently open and will reappear when that pit is loaded again.`);
    const extra=notes.length?'\n\n'+notes.join('\n'):'';
    if(!confirm('Replace the current form contents with pit "'+pitId+'"?'+extra))return;
    if(queued&&!_loaded_site_id&&typeof discardAttachmentQueue==='function'){
      try{await discardAttachmentQueue({rotateDraft:true});}
      catch(e){setst('Could not clear the current local photo queue: '+e.message,'err');return;}
    }
  }
  return apiFetch('/api/load/'+encodeURIComponent(siteId))
    .then(r=>r.json()).then(async r=>{
      if(!r.ok){setst('load error: '+r.msg,'err');return;}
      populate(r.pit);
      setRecordMode(r.site_id,r.pit_id,false,false);
      if(typeof switchAttachmentQueueContext==='function')await switchAttachmentQueueContext(r.site_id);
      dismissPostArchive();
      if(typeof refreshAttachUI==='function')refreshAttachUI();
      scheduleDraft();
      if(typeof openRecord==='function')openRecord({focus:false});
      if(typeof refreshWorkspaceCurrent==='function')refreshWorkspaceCurrent();
      setchip('● editing · '+r.pit_id,'ok');
      setst('Loaded '+r.pit_id+'. Archive Changes updates this existing record.','ok');
    }).catch(err=>setst(fetchErr(err),'err'));
}
