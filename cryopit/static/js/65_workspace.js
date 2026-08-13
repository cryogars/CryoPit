/* Lightweight unit tests load modules independently; the assembled app gets
   the CSRF-aware implementation from 00_core.js. */
if(typeof apiFetch==='undefined'){var apiFetch=(path,options)=>fetch((typeof API==='undefined'?'':API)+path,options);}

// Stage 11 operational workspace -------------------------------------------
// The workspace and field form live in the same page. Switching views never
// destroys the form, the Stage 10 finder state, or the IndexedDB photo outbox.
let _workspaceSummary=null;
let _workspaceLoading=false;
let _workspacePhotoTimer=null;

function _workspaceEl(id){return document.getElementById(id);}
function _workspaceText(className,text){
  const span=document.createElement('span');span.className=className;span.textContent=text;return span;
}
function _workspaceContext(){
  const bits=[];
  const site=gv('site').trim();
  const campaign=gv('campaign').trim();
  const date=gv('date').trim();
  if(site)bits.push(site);if(campaign)bits.push(campaign);if(date)bits.push(date);
  return bits.join(' · ');
}
function refreshWorkspaceCurrent(){
  const card=_workspaceEl('workspace-current');if(!card)return;
  const dirty=(typeof formDirty==='function')&&formDirty();
  if(!_loaded_site_id&&!dirty){card.hidden=true;return;}
  card.hidden=false;
  const label=_workspaceEl('workspace-current-title');
  const pit=_workspaceEl('workspace-current-pit');
  const detail=_workspaceEl('workspace-current-detail');
  const continueButton=_workspaceEl('workspace-current-continue');
  if(label)label.textContent=_loaded_site_id?'Currently editing':'Current draft';
  if(continueButton){
    const continueLabel=_loaded_site_id?'Continue record':'Continue draft';
    continueButton.textContent=continueLabel;
    continueButton.setAttribute('aria-label',continueLabel);
  }
  const visible=document.getElementById('pitid')?.textContent?.trim();
  if(pit)pit.textContent=(visible&&visible!=='—')?visible:(_loaded_pit_id||'Unarchived pit');
  const context=_workspaceContext();
  if(detail)detail.textContent=context||(_loaded_site_id?'Archived record loaded':'Not yet archived');
}
function openWorkspace(opts={}){
  const workspace=_workspaceEl('workspace'),shell=_workspaceEl('app-shell');
  if(!workspace||!shell)return;
  workspace.hidden=false;shell.hidden=true;
  document.body.classList.add('workspace-open');
  if(typeof toggleNav==='function')toggleNav(false);
  refreshWorkspaceCurrent();
  if(!_workspaceSummary||opts.reload)loadWorkspaceSummary();
  refreshWorkspacePhotoQueue();
  if(opts.focus!==false){try{workspace.focus({preventScroll:true});}catch(e){workspace.focus();}}
}
function openRecord(opts={}){
  const workspace=_workspaceEl('workspace'),shell=_workspaceEl('app-shell');
  if(!workspace||!shell)return;
  workspace.hidden=true;shell.hidden=false;
  document.body.classList.remove('workspace-open');
  if(opts.focus!==false){const main=_workspaceEl('main');if(main)main.focus();}
}
function openSavedPitsFinder(){
  if(!ENABLE_EDIT){setst('Saved pits are disabled in this deployment.','err');return;}
  openRecord({focus:false});
  if(typeof toggleIndex==='function')toggleIndex(false);
  if(globalThis.matchMedia&&matchMedia('(max-width:900px)').matches&&typeof toggleNav==='function')toggleNav(true);
  const search=_workspaceEl('saved-pits-search');
  if(search){search.focus();try{search.scrollIntoView({block:'center'});}catch(e){}}
}
function openAttachmentsSection(){
  openRecord({focus:false});
  const item=document.querySelector('[data-t="s11"]');
  if(item&&typeof item.click==='function')item.click();
  const section=_workspaceEl('s11');if(section)try{section.focus({preventScroll:true});}catch(e){}
}
function openWorkspacePhotoQueue(){
  const current=(typeof formDirty==='function'&&formDirty())||_loaded_site_id;
  if(current)openAttachmentsSection();else openSavedPitsFinder();
}
async function workspaceStartNewPit(){
  const dirty=(typeof formDirty==='function')&&formDirty();
  if(dirty||_loaded_site_id){await newPit();return;}
  openRecord({focus:false});
  const first=_workspaceEl('loc')||_workspaceEl('site');if(first)first.focus();
}

function _renderWorkspaceRecent(pits){
  const box=_workspaceEl('workspace-recent');if(!box)return;
  box.innerHTML='';
  if(!pits.length){box.appendChild(_workspaceText('workspace-empty','No archived pits yet.'));return;}
  pits.forEach(p=>{
    const button=document.createElement('button');button.type='button';button.className='workspace-pit';
    const copy=document.createElement('span');copy.className='workspace-pit-copy';
    const name=document.createElement('strong');name.textContent=p.pit_id||'Unnamed pit';copy.appendChild(name);
    const context=[p.site,p.campaign,p.date].filter(Boolean).join(' · ');
    if(context)copy.appendChild(_workspaceText('',context));
    button.appendChild(copy);
    const status=[];
    if(p.pending_photos)status.push(`${p.pending_photos} photo${p.pending_photos===1?'':'s'} pending`);
    if(p.missing_attachments)status.push(`${p.missing_attachments} missing`);
    button.appendChild(_workspaceText('workspace-pit-status',status.join(' · ')||'archived'));
    button.addEventListener('click',()=>loadPit(p.site_id,p.pit_id));
    box.appendChild(button);
  });
}
function _renderWorkspaceRecovery(pending,missing=0){
  const box=_workspaceEl('workspace-recovery'),count=_workspaceEl('workspace-recovery-count');
  if(!box)return;
  box.innerHTML='';if(count)count.textContent=String(pending.length+Number(missing||0));
  if(!pending.length&&!missing){box.appendChild(_workspaceText('workspace-empty','No archive or attachment operations need attention.'));return;}
  pending.slice(0,4).forEach(p=>{
    const button=document.createElement('button');button.type='button';button.className='workspace-recovery-item';
    const copy=document.createElement('span');copy.className='workspace-recovery-copy';
    const name=document.createElement('strong');name.textContent=p.pit_id||'Unnamed pit';copy.appendChild(name);
    copy.appendChild(_workspaceText('',p.updated_at?`Interrupted · ${String(p.updated_at).slice(0,10)}`:'Interrupted archive operation'));
    button.appendChild(copy);button.appendChild(_workspaceText('workspace-pit-status','Recover'));
    button.addEventListener('click',()=>recoverPit(p.site_id,p.pit_id,button));
    box.appendChild(button);
  });
  if(missing){
    const button=document.createElement('button');button.type='button';button.className='workspace-recovery-item';
    const copy=document.createElement('span');copy.className='workspace-recovery-copy';
    const name=document.createElement('strong');name.textContent='Attachment integrity';copy.appendChild(name);
    copy.appendChild(_workspaceText('',`${missing} stored attachment file${missing===1?' is':'s are'} missing`));
    button.appendChild(copy);button.appendChild(_workspaceText('workspace-pit-status','Find'));
    button.addEventListener('click',openSavedPitsFinder);box.appendChild(button);
  }
}
function loadWorkspaceSummary(){
  if(!ENABLE_EDIT){
    _renderWorkspaceRecent([]);_renderWorkspaceRecovery([],0);
    const find=_workspaceEl('workspace-find');if(find)find.hidden=true;
    return Promise.resolve();
  }
  if(_workspaceLoading)return Promise.resolve();
  _workspaceLoading=true;
  const recent=_workspaceEl('workspace-recent'),recovery=_workspaceEl('workspace-recovery');
  if(recent)recent.setAttribute('aria-busy','true');if(recovery)recovery.setAttribute('aria-busy','true');
  return apiFetch('/api/workspace')
    .then(r=>{if(!r.ok)throw new Error('workspace request failed ('+r.status+')');return r.json();})
    .then(r=>{
      _workspaceSummary=r;_renderWorkspaceRecent(r.recent||[]);_renderWorkspaceRecovery(r.recovery||[],Number(r.missing_attachments||0));
      refreshWorkspacePhotoQueue();
    })
    .catch(err=>{
      if(recent){recent.innerHTML='';recent.appendChild(_workspaceText('workspace-empty','Could not load recent pits.'));}
      if(recovery){recovery.innerHTML='';recovery.appendChild(_workspaceText('workspace-empty','Could not check recovery state.'));}
      setst('Workspace: '+err.message,'err');
    })
    .finally(()=>{
      _workspaceLoading=false;
      if(recent)recent.setAttribute('aria-busy','false');if(recovery)recovery.setAttribute('aria-busy','false');
    });
}
async function _refreshWorkspacePhotoQueueNow(){
  const count=_workspaceEl('workspace-photo-count');
  const summaryEl=_workspaceEl('workspace-photo-summary');
  const action=_workspaceEl('workspace-photo-action');
  if(!count||!summaryEl)return;
  let local={total:(typeof _pendingTotal==='function'?_pendingTotal():0),contexts:0,available:true,persistence:'unknown'};
  try{
    if(typeof attachmentOutboxAllSummary==='function')local=await attachmentOutboxAllSummary();
  }catch(e){local.available=false;}
  const expected=Number(_workspaceSummary?.expected_photos||0);
  count.textContent=String(local.total||0);
  if(!local.available){
    summaryEl.textContent='Durable browser photo storage is unavailable. Files selected now survive only while this page remains open.';
  }else if(local.total){
    const contexts=local.contexts||1;
    const storage=local.persistence==='persistent'?'persistent storage':'browser storage';
    summaryEl.textContent=`${local.total} photograph${local.total===1?'':'s'} queued across ${contexts} pit or draft context${contexts===1?'':'s'} in ${storage}.`;
  }else{
    summaryEl.textContent='No photographs are waiting in this browser.';
  }
  if(expected){
    summaryEl.textContent+=` The server expects ${expected} pending photograph${expected===1?'':'s'} across your pits.`;
  }
  if(action)action.hidden=!(local.total||expected);
}
function refreshWorkspacePhotoQueue(){
  clearTimeout(_workspacePhotoTimer);
  _workspacePhotoTimer=setTimeout(()=>_refreshWorkspacePhotoQueueNow().catch(()=>{}),0);
}
function initWorkspace(){
  document.body.classList.add('workspace-open');
  refreshWorkspaceCurrent();
  loadWorkspaceSummary();
  refreshWorkspacePhotoQueue();
}
