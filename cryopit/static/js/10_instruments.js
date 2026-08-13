function buildInst(){
  let h='',ii=0,open=false,groupHasSN=false;
  // Look ahead from a group header to see if any of its rows take a serial.
  // Instruments do (3-column table); Surveys & documentation don't (2-column).
  const groupTakesSN=(startIdx)=>{
    for(let k=startIdx+1;k<INST.length && !INST[k].g;k++) if(INST[k].sn) return true;
    return false;
  };
  INST.forEach((it,idx)=>{
    if(it.g){
      if(open)h+='</tbody></table>';
      groupHasSN=groupTakesSN(idx);
      const head = groupHasSN
        ? `<th style="width:46%">Instrument</th><th>Serial no.</th><th>Used (Y/N)</th>`
        : `<th style="width:70%">Survey / documentation</th><th>Used (Y/N)</th>`;
      // Each group carries its own "none of these" affirmation. Rows begin
      // unanswered (neither button lit); ticking this states the negative
      // explicitly by setting every row in the group to N and locking it.
      const gid=it.g.toLowerCase().startsWith('instrument')?'inst':'task';
      const gLabel=gid==='inst'?'No instruments used':'No tasks done';
      h+=`<div class="ig-lbl">${it.g}`
       + `<label class="ig-none"><input type="checkbox" id="none-${gid}" onchange="onNoneGroup('${gid}',!this.checked)">`
       + `<span>${gLabel}</span></label></div>`
       + `<table class="it" data-group="${gid}"><thead><tr>${head}</tr></thead><tbody>`;
      open=true;
    } else {
      const i=ii++;
      // SN cell only exists in SN-bearing groups. It starts disabled because
      // the row is unanswered; a serial can be entered only after an explicit Y.
      // Survey/documentation rows have no SN cell at all.
      const snCell = groupHasSN
        ? `<td>${it.sn ? `<input class="sn" id="sn${i}" placeholder="" aria-label="${it.n||'Other'} serial number" disabled>` : '—'}</td>`
        : '';
      const nameCell = it.w
        ? `<td><input id="on${i}" placeholder="Other instrument…" aria-label="Other instrument name" oninput="tick()" style="border:none;background:transparent;font-family:var(--sans);font-size:12px;color:var(--ink);width:100%;outline:none"></td>`
        : `<td>${it.n}</td>`;
      h+=`<tr>${nameCell}${snCell}
          <td><div class="yn" role="group" aria-label="${it.n||'Other'} used">
          <button type="button" class="y" id="yy${i}" aria-pressed="false" onclick="pickyn(${i},'Y')">Y</button>
          <button type="button" class="n" id="yn${i}" aria-pressed="false" onclick="pickyn(${i},'N')">N</button></div></td></tr>`;
    }
  });
  if(open)h+='</tbody></table>';
  document.getElementById('ig').innerHTML=h;
  window._ic=ii;
}
// User clicks are true three-state toggles: clicking the currently selected
// answer again retracts it to unanswered. Programmatic restore/locking calls
// setyn() directly so they remain deterministic rather than toggle-sensitive.
function pickyn(i,v){
  const btn=document.getElementById((v==='Y'?'yy':'yn')+i);
  setyn(i,btn?.classList.contains('on')?null:v);
}

function setyn(i,v){
  const bY=document.getElementById('yy'+i),bN=document.getElementById('yn'+i);
  if(!bY||!bN)return;
  const state=v==='Y'?'Y':(v==='N'?'N':null);
  bY.classList.toggle('on',state==='Y');
  bN.classList.toggle('on',state==='N');
  bY.setAttribute('aria-pressed',String(state==='Y'));
  bN.setAttribute('aria-pressed',String(state==='N'));
  // The serial field is only usable when the instrument is explicitly Y.
  // N and unanswered both disable and clear it, so stale serial data cannot
  // survive invisibly after an answer is retracted or changed.
  const sn=document.getElementById('sn'+i);
  if(sn){
    sn.disabled = (state!=='Y');
    if(state!=='Y') sn.value='';
  }
  tick();
  if(typeof refreshAttachUI==='function')refreshAttachUI();
}
function so(a){return a.map(v=>`<option value="${v}">${v}</option>`).join('')}



// "No instruments used" / "No tasks done": one explicit statement per group.
// Ticking it forces every row in that group to N and locks them — a pit cannot
// simultaneously claim nothing was used AND list something that was.
function onNoneGroup(gid,retract){
  // `retract` is true ONLY when a person unticks the box. populate() also calls
  // this to reapply the lock after loading a pit, and must not wipe the rows it
  // just restored.
  const off=document.getElementById('none-'+gid)?.checked;
  const tbl=document.querySelector(`table.it[data-group="${gid}"]`);
  if(tbl){
    tbl.querySelectorAll('tbody tr').forEach(tr=>{
      const yBtn=tr.querySelector('.yn button.y');
      if(!yBtn)return;
      const i=+yBtn.id.replace('yy','');
      if(off){
        setyn(i,'N');
      }else if(retract){
        // Unticking RETRACTS the statement, so rows return to unanswered
        // instead of retaining N answers the user did not choose individually.
        setyn(i,null);
      }
      tr.querySelectorAll('.yn button').forEach(b=>b.disabled=!!off);
      const on=tr.querySelector('input[id^="on"]');
      if(on){on.disabled=!!off;if(!off)on.value=on.value;}
    });
    tbl.classList.toggle('group-none',!!off);
  }
  // this checkbox opens and closes the photo inputs and the §7 cameras, so
  // both have to be repainted here — tick() alone does not touch them
  if(typeof refreshAttachUI==='function')refreshAttachUI();
  if(typeof refreshLayerCams==='function')refreshLayerCams();
  tick();
}

// A group is ANSWERED when it has at least one Yes, or the user has explicitly
// said it has none. Silence is not an answer.
function groupAnswered(gid){
  if(document.getElementById('none-'+gid)?.checked)return true;
  const tbl=document.querySelector(`table.it[data-group="${gid}"]`);
  return !!tbl && tbl.querySelectorAll('.yn button.y.on').length>0;
}
function instChecklistDone(){return groupAnswered('inst')&&groupAnswered('task');}

// ---------------------------------------------------------------------------
// The checklist REFLECTS evidence rather than gating it.
//
// Where the data identifies the instrument, the row is forced and locked — you
// cannot claim you took no stratigraphy photographs while four sit in the
// folder. Where it does not, we warn instead of guessing: LWC readings come
// from either a Digital LWC or a Lyte Probe and the form does not say which, so
// auto-ticking one would put a statement in the record that nobody made.
//
// Nothing ever flips under the user: in each state the contradictory action was
// never available, which is why no "the app changed this" notice is needed.
// ---------------------------------------------------------------------------
function _rowIndex(name){
  let di=0,found=null;
  INST.forEach(it=>{if(it.g)return;const i=di++;if(it.n===name)found=i;});
  return found;
}
function _forceRow(name,reason){
  const i=_rowIndex(name); if(i===null)return;
  const y=document.getElementById('yy'+i),n=document.getElementById('yn'+i);
  if(!y||!n)return;
  const row=y.closest('tr');
  let note=row&&row.querySelector('.lock-note');
  if(reason){
    if(!y.classList.contains('on'))setyn(i,'Y');
    y.disabled=n.disabled=true;
    y.title=n.title=reason;
    // Re-read the note AFTER setyn(). setyn() calls tick(), which can re-enter
    // syncChecklistFromEvidence() synchronously — so the inner call created the
    // note while this frame still held the `note = null` it had read moments
    // earlier, and then created a second identical one. That is why the lock
    // line appeared twice on the rows that had photographs.
    note=row&&row.querySelector('.lock-note');
    // Say it on screen. A tooltip is invisible to anyone who does not happen to
    // hover, and on a tablet there is no hover at all.
    if(row&&!note){
      note=document.createElement('div');
      note.className='lock-note';
      const cell=row.querySelector('td');
      if(cell)cell.appendChild(note);
    }
    if(note)note.textContent='🔒 '+reason;
    if(row)row.classList.add('is-locked');
  }else{
    y.disabled=n.disabled=false;
    y.removeAttribute('title');n.removeAttribute('title');
    if(note)note.remove();
    if(row)row.classList.remove('is-locked');
  }
}
function syncChecklistFromEvidence(){
  const counts=(typeof _attachInfo==='object'&&_attachInfo.counts)||{};
  const pend=(typeof _pendingAttach==='object')?_pendingAttach:{sheet:[],pitwall:[],stratigraphy:[]};
  const nPit=(counts.pitwall||0)+(pend.pitwall||[]).length;
  const nStr=(counts.stratigraphy||0)+(pend.stratigraphy||[]).length;
  _forceRow('Pit pictures',  nPit?`${nPit} pit-wall photo${nPit>1?'s':''} attached. Remove them to change this.`:'');
  _forceRow('Stratigraphy pictures', nStr?`${nStr} layer photo${nStr>1?'s':''} attached. Remove them to change this.`:'');

  // SSA is 1:1: §8's instrument list is IceCube / IRIS2 / IRIS, all of which
  // ARE the "SSA / NIR Box". If there are SSA measurements, that box was used.
  const ssaRows=[...document.querySelectorAll('#ssab tr')]
    .filter(tr=>{const i=tr.querySelector('input');return i&&i.value.trim()!=='';}).length;
  _forceRow('SSA / NIR Box', ssaRows?`${ssaRows} SSA measurement${ssaRows>1?'s':''} recorded in §8`:'');

  // A group cannot be declared empty while it holds evidence.
  const noTask=document.getElementById('none-task');
  if(noTask){
    const busy=nPit||nStr;
    noTask.disabled=!!busy;
    noTask.parentElement.title=busy?'Photos are attached. Remove them first.':'';
    if(busy&&noTask.checked){noTask.checked=false;onNoneGroup('task');}
  }
}
