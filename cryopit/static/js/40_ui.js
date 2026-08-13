// Theme --------------------------------------------------------------
function toggleTheme(){
  const dark=document.documentElement.getAttribute('data-theme')==='dark';
  document.documentElement.setAttribute('data-theme',dark?'light':'dark');
  document.getElementById('theme-btn').textContent=dark?'◑':'◐';
  try{localStorage.setItem('cp-theme',dark?'light':'dark');}catch(e){}
}
(function(){
  try{
    const t=localStorage.getItem('cp-theme');
    if(t==='dark'){document.documentElement.setAttribute('data-theme','dark');document.getElementById('theme-btn').textContent='◐';}
  }catch(e){}
})();

// Which sections currently show a blocking / warning box.
function _blockingSections(){
  const blocking=new Set();
  document.querySelectorAll('.warn-box.has-block').forEach(box=>{
    if(box.style.display==='none')return;
    const sec=box.closest('.sec'); if(sec)blocking.add(sec.id);
  });
  return blocking;
}

// Section headers carry the same state as the sidebar pips, and are the ONLY
// thing visible when a section is collapsed.
//
// This is deliberately SEPARATE from tick(). The warning boxes are recomputed
// by densityWarnings(), which runs 300 ms after tick() via scheduleMini() — so
// a tick() that read them saw the state from BEFORE the keystroke that caused
// it. A new error appeared only on the next unrelated edit, and a corrected one
// stayed red until something else was typed. Anything that changes a warning
// box calls this directly.
let _lastPips={};
function refreshStatusGlyphs(){
  const blocking=new Set(), warning=new Set();
  document.querySelectorAll('.warn-box').forEach(box=>{
    if(box.style.display==='none')return;
    const sec=box.closest('.sec'); if(!sec)return;
    if(box.classList.contains('has-block'))blocking.add(sec.id);
    else if(box.classList.contains('has-warn'))warning.add(sec.id);
  });
  Object.entries(_lastPips).forEach(([id,v])=>{
    const e=document.getElementById(id);
    const secId='s'+id.slice(1);
    const state=blocking.has(secId)?'block':warning.has(secId)?'warn':(v?'done':'');
    if(e){e.classList.toggle('done',!!v);
      e.classList.toggle('warn',state==='warn');
      e.classList.toggle('block',state==='block');}
    setSecStatus(secId,state);
  });
  // The §12 checklist rows are built by tick(), which does not re-run when a
  // warning box changes — so a blocking section stayed green there even once
  // its header glyph had gone red. Repaint them here rather than calling
  // tick(), which would loop back through scheduleMini -> densityWarnings.
  // The checklist carries the SAME three states as the header glyph and the
  // sidebar pip. It previously knew only about blockers, so a section with
  // warnings showed a plain green tick here while its header said ⚠ — the one
  // place a reader is most likely to be scanning for what still needs doing.
  document.querySelectorAll('#cl-items .ci').forEach(row=>{
    const bad=blocking.has(row.dataset.t);
    const warn=!bad&&warning.has(row.dataset.t);
    const dot=row.querySelector('.cd'), txt=row.querySelector('.ct');
    if(dot){
      dot.classList.toggle('bad',bad);
      dot.classList.toggle('warn',warn);
      dot.textContent=bad?'✖':(warn?'⚠':'');
    }
    if(txt){txt.classList.toggle('bad',bad);txt.classList.toggle('warn',warn);}
  });
}

// One glyph per section header. ✓ complete · ⚠ warnings · ✖ blocks archive ·
// nothing when the section is simply untouched.
const _SEC_GLYPH={done:['✓','complete'],warn:['⚠','has warnings'],
                  block:['✖','blocks archive']};
function setSecStatus(secId,state){
  const sec=document.getElementById(secId); if(!sec)return;
  const hd=sec.querySelector('.sec-hd'); if(!hd)return;
  let el=hd.querySelector('.sec-status');
  if(!el){
    el=document.createElement('span');
    el.className='sec-status';
    // the reason belongs to the glyph, so hovering a collapsed header explains
    // itself without expanding it
    el.setAttribute('role','status');
    // directly after the title, BEFORE the ⇅ sort button — it describes the
    // section, so it reads as part of the name rather than as another control
    const title=hd.querySelector('.sec-title');
    const anchor=title&&(title.closest('.sec-heading')||title);
    if(anchor&&anchor.nextSibling)hd.insertBefore(el,anchor.nextSibling);
    else hd.appendChild(el);
  }
  const g=_SEC_GLYPH[state];
  el.textContent=g?g[0]:'';
  el.className='sec-status'+(state?' is-'+state:'');
  if(g){el.title=sec.querySelector('.sec-title')?.textContent+': '+g[1];
        el.setAttribute('aria-label',g[1]);}
  else {el.removeAttribute('title');el.removeAttribute('aria-label');}
}

// Collapse the section index on wide screens. Distinct from toggleNav(), which
// is the <900px drawer: this narrows the column in place so the form gets the
// width back without the index disappearing entirely — the numbers stay
// visible as a spine you can still click.
function toggleIndex(force){
  const on=(force!==undefined)?force:!document.body.classList.contains('index-collapsed');
  document.body.classList.toggle('index-collapsed',on);
  const b=document.getElementById('idx-collapse');
  if(b){
    b.textContent=on?'»':'«';
    b.setAttribute('aria-expanded',String(!on));
    b.title=(on?'Expand':'Collapse')+' the section index (⌘/Ctrl + \\)';
  }
  try{localStorage.setItem('cryopit-index-collapsed',on?'1':'0');}catch(e){}
}
document.addEventListener('keydown',e=>{
  if((e.metaKey||e.ctrlKey)&&e.key==='\\'){e.preventDefault();toggleIndex();}
});

// Nav ----------------------------------------------------------------
// Below 900px the index is a drawer rather than a permanent column.
function toggleNav(force){
  const open=(force!==undefined)?force:!document.body.classList.contains('nav-open');
  document.body.classList.toggle('nav-open',open);
  const btn=document.getElementById('nav-toggle');
  if(btn){btn.setAttribute('aria-expanded',String(open));
    btn.setAttribute('aria-label',open?'Hide section index':'Show section index');}
}
// The rail opens §10 and renders. Extracted from an inline handler so the
// keyboard path and the click path run exactly the same code.
function openFullProfile(){
  const item=document.querySelector('[data-t="s10"]');
  if(item)item.click();
  if(typeof drawProfile==='function')drawProfile();
}
document.addEventListener('keydown',e=>{
  if(e.key==='Escape'&&document.body.classList.contains('nav-open'))toggleNav(false);
});
function nav(el){expandSection(el.dataset.t);
  document.querySelectorAll('.idx-item').forEach(n=>{
    n.classList.remove('active');n.removeAttribute('aria-current');});
  el.classList.add('active');
  el.setAttribute('aria-current','true');
  const target=document.getElementById(el.dataset.t);
  const main=document.querySelector('.main');
  const top=target.getBoundingClientRect().top
            -main.getBoundingClientRect().top
            +main.scrollTop;
  main.scrollTo({top,behavior:'smooth'});
  if(window.matchMedia('(max-width:900px)').matches)toggleNav(false);
}

let _pe=false;
function onPitEdit(){
  _pe=true;
  const v=document.getElementById('pitid').textContent.trim();
  document.getElementById('tb-pid').textContent=v||'—';
  // A loaded form stays bound to its immutable site_id. Correcting the visible
  // Pit ID updates that same record; CryoPit never treats an archived pit as a
  // template or silently forks it.
  const hint=document.getElementById('pidhint');
  if(hint){
    if(_loaded_site_id && v && v!==_loaded_pit_id){
      hint.textContent='identifier correction: Archive Changes updates this same pit.';
      hint.classList.add('pid-new');
    }else if(_loaded_site_id){
      hint.textContent='editing saved pit: Archive Changes updates this record.';
      hint.classList.remove('pid-new');
    }else{
      hint.textContent='auto: site + date. Tap to edit.';
      hint.classList.remove('pid-new');
    }
  }
  if(typeof refreshWorkspaceCurrent==='function')refreshWorkspaceCurrent();
  tick();
}
function onLoc(){const v=document.getElementById('loc').value;document.getElementById('loc-c').style.display=v==='__c'?'block':'none';updateId();tick();}
function updateId(){
  if(_pe)return;
  const loc=document.getElementById('loc').value;
  const site=document.getElementById('site').value.trim();
  const d=document.getElementById('date').value;
  // Pit ID is site + date. BOTH are required — without a site we'd generate a
  // malformed id like "PIT20260210" that could collide across pits. So if either
  // is missing, leave the id as a placeholder rather than auto-generating.
  if(!d || !site){
    document.getElementById('pitid').textContent='—';
    document.getElementById('tb-pid').textContent='—';
    tick();
    return;
  }
  const ds=d.replace(/-/g,''),sc=site.replace(/\W+/g,'').toUpperCase();  // full site name — no truncation (SNOW STAKE -> SNOWSTAKE...)
  const id=sc+ds;
  document.getElementById('pitid').textContent=id;
  document.getElementById('tb-pid').textContent=id;
  tick();
}
function gv(id){return(document.getElementById(id)||{}).value||''}
function gr(name){const r=document.querySelector(`input[name="${name}"]:checked`);return r?r.value:''}
function gcs(name){
  return [...document.querySelectorAll(`input[name="${name}"]:checked`)].map(r=>r.value);
}


// Optional radio groups -------------------------------------------------------
// Native radio buttons intentionally remain native: clicking the selected
// option again does not deselect it. Optional groups instead receive one
// explicit, consistently named action that returns the group to unanswered.
// Only groups marked data-clearable-radio are enhanced, so a future required
// radio group cannot accidentally be made clearable by this generic helper.
function refreshClearableRadioGroups(){
  document.querySelectorAll('[data-clearable-radio]').forEach(group=>{
    const button=group.parentElement?.querySelector(':scope > .radio-clear');
    if(!button)return;
    button.hidden=!group.querySelector('input[type="radio"]:checked');
  });
}
function initClearableRadios(){
  document.querySelectorAll('[data-clearable-radio]').forEach(group=>{
    if(group.dataset.clearableReady==='1')return;
    const radios=[...group.querySelectorAll('input[type="radio"][name]')];
    if(!radios.length)return;
    const name=radios[0].name;
    if(!name||radios.some(r=>r.name!==name))return;
    group.dataset.clearableReady='1';
    const card=group.closest('.ri');
    const label=(card?.querySelector(':scope > .rl')?.textContent||name)
      .replace(/\s*\*\s*/g,' ').replace(/\s+/g,' ').trim();
    const button=document.createElement('button');
    button.type='button';
    button.className='radio-clear';
    button.textContent='Clear selection';
    button.setAttribute('aria-label',`Clear ${label} selection`);
    group.insertAdjacentElement('afterend',button);
    const sync=()=>{button.hidden=!radios.some(r=>r.checked);};
    radios.forEach(r=>r.addEventListener('change',sync));
    button.addEventListener('click',()=>{
      const selected=radios.find(r=>r.checked);
      if(!selected)return;
      selected.checked=false;
      // Dispatch through the existing form listeners so pill styling,
      // completion, draft autosave and the live profile all stay in sync.
      selected.dispatchEvent(new Event('change',{bubbles:true}));
      sync();
      // The button disappears once the group is unanswered. Return focus to
      // the native radio group without scrolling the field card.
      try{radios[0].focus({preventScroll:true});}catch(e){radios[0].focus();}
    });
    sync();
  });
}

function tick(){
  const loc=document.getElementById('loc').value;
  const lok=loc&&loc!=='__c'||document.getElementById('loc-c').value.trim();
  const pid=document.getElementById('pitid').textContent.trim();
  // A section counts as done when it holds DATA, not when it holds a row.
  // These tested `children.length>0`, so clicking "+ add measurement" and
  // typing nothing turned the dot green — the dot certified that a button had
  // been pressed.
  const hasData=(bodyId,cols)=>[...document.querySelectorAll('#'+bodyId+' tr')]
    .some(tr=>{
      const ins=tr.querySelectorAll('input');
      return cols.every(i=>ins[i]&&ins[i].value.trim()!=='');
    });
  const tempOK  = hasData('tb',[0,1]);          // height + temperature
  const densOK  = hasData('db',[0,1,2]);        // interval + at least reading A
  const lwcOK   = hasData('lb',[0,1]);          // interval present
  const stratOK = hasData('sb',[0,1]);          // interval present
  const ssaOK   = hasData('ssab',[0]);          // at least a height

  // Which measurement sections this pit NEEDS is declared in §9. A dry
  // midwinter pit has no LWC to record, and was previously stuck below 100%
  // for not reporting one; SSA was never counted at all, so a full SSA profile
  // moved the bar not at all. The checklist is where the surveyor states what
  // they did, so it decides what counts.
  const declared=n=>(typeof _checklistYes==='function')&&_checklistYes(n);
  const wantLWC = declared('Digital LWC')||declared('Lyte Probe')||lwcOK;
  const wantSSA = declared('SSA / NIR Box')||ssaOK;

  // §9 is complete when BOTH groups have been answered — a Yes somewhere, or
  // an explicit "none". Computed before chk[] because it is a member of it.
  const instDone=(typeof instChecklistDone==='function')?instChecklistDone():false;
  const chk=[
    lok&&pid&&pid!=='—',
    gv('recby').trim(), gv('surv').trim(),
    gv('utme').trim()||gv('lat').trim(),
    gcs('pr').length>0, gr('gc'),
    tempOK, densOK, stratOK,
    instDone,        // §9 counts: it is real work, and the section a crew is
                     // most likely to skip because it sits last and looks
                     // like admin. Without it the bar reached 100% with the
                     // checklist untouched.
  ];
  // conditional members join the denominator only once this pit wants them
  if(wantLWC)chk.push(lwcOK);
  if(wantSSA)chk.push(ssaOK);
  const done=chk.filter(Boolean).length,pct=Math.round(done/chk.length*100);
  document.getElementById('tb-fill').style.width=pct+'%';
  document.getElementById('tb-pct').textContent=pct+'%';
  const prog=document.getElementById('tb-prog');
  prog.title='Completion: '+pct+'%';
  prog.setAttribute('aria-valuenow',String(pct));
  prog.setAttribute('aria-valuetext',pct+'% complete');
  const ssaDone=ssaOK;
  // §9 is complete when BOTH groups have been answered — a Yes somewhere, or
  // an explicit "none". Previously any single Yes marked the whole section
  // done, and an all-N pit could never go green at all.
  // §11 holds DATA — photographs go into the archive — so it carries the same
  // indicator as every other data section: filled once it holds something.
  // It is deliberately absent from `chk` above: attachments are optional, so
  // counting them would mean a pit with no photographs could never reach 100%.
  //
  // §10 has no indicator ON PURPOSE. It is a viewer, not a section you fill in.
  // The figure in the archive is rendered from the payload at download time
  // whether or not anyone pressed "render profile", so a tick there would
  // certify that someone had looked at a preview — and its absence would
  // suggest something was missing when nothing is.
  const pips={p1:chk[0]&&chk[1]&&chk[2]&&chk[3],p2:chk[4],p3:chk[5],
    p4:tempOK,p5:densOK,p6:lwcOK,p7:stratOK,p8:ssaDone,p9:instDone,
    p11:(typeof attachCount==='function')&&attachCount()>0,
    p12:pct===100};
  _lastPips=pips;
  const blocking=_blockingSections();
  // NOTE: refreshStatusGlyphs() is called at the END of tick(), not here.
  // The §12 checklist rows are rebuilt further down via innerHTML, which
  // discarded any classes painted before that point — so a warned section
  // showed a plain green tick in the checklist while its header said ⚠.
  document.getElementById('cl-pct').textContent=pct+'%';
  document.getElementById('cl-fill').style.width=pct+'%';
  // "sections" was wrong: four of these live inside §1 alone.
  document.getElementById('cl-lbl').textContent=`${done} of ${chk.length} required items`;
  // Section numbers so an unchecked item says WHERE to go, not just what is
  // missing. Order must track chk[] exactly.
  const labels=[['01','Location & Pit ID'],['01','Recorded by'],['01','Field observers'],
    ['01','Coordinates'],['02','Weather'],['03','Ground'],['04','Temperature'],
    ['05','Density'],['07','Stratigraphy'],['09','Instruments & tasks']]
    .concat(wantLWC?[['06','LWC']]:[]).concat(wantSSA?[['08','SSA']]:[]);
  // keep chk[] and labels[] paired while sorting into section order, so a
  // conditional item appears where the form actually puts it
  const rows=labels.map((l,i)=>({num:l[0],label:l[1],ok:chk[i]}))
    .sort((a,b)=>a.num.localeCompare(b.num));
  // Each row is a button: seeing what is missing and going to fix it should
  // not be two separate acts of navigation.
  document.getElementById('cl-items').innerHTML=rows.map(({num,label:l,ok})=>{
    const bad=blocking.has('s'+ +num);
    const cls=bad?' bad':(ok?' done':'');
    return `<button type="button" class="ci" data-t="s${+num}" onclick="nav(this)"
      title="Go to section ${num}: ${esc(l)}">`
      +`<div class="cd${cls}">${bad?'✖':''}</div>`
      +`<span class="cl-num">${num}</span>`
      +`<span class="ct${cls}">${esc(l)}</span></button>`;
  }).join('');
  // after the checklist rows are built, so their state survives
  refreshStatusGlyphs();
  scheduleDraft();
  scheduleMini();
}


// Collapsible sections: click a section header to fold its body. Collapsed
// sections stay in the DOM, so collect(), validation, drafts, and the
// progress bar are unaffected. Nav clicks auto-expand their target.
function initCollapse(){
  document.querySelectorAll('.sec-hd').forEach(hd=>{
    hd.classList.add('collapsible');
    const sec=hd.closest('.sec');
    const body=sec&&sec.querySelector('.sec-body');
    // The header was click-only: a keyboard user could not collapse a section
    // at all, and nothing announced whether one was open. It is a real control,
    // so it gets a role, a tab stop, a state, and Enter/Space.
    hd.setAttribute('role','button');
    hd.setAttribute('tabindex','0');
    hd.setAttribute('aria-expanded','true');
    if(body){
      if(!body.id)body.id=(sec.id||'sec')+'-body';
      hd.setAttribute('aria-controls',body.id);
    }
    const isPassthrough=t=>t.closest('button,a,input,select,label,textarea');
    const toggle=()=>{
      const collapsed=sec.classList.toggle('collapsed');
      hd.setAttribute('aria-expanded',String(!collapsed));
    };
    hd.addEventListener('click',e=>{
      if(isPassthrough(e.target))return;   // §7 ρ toggle, ⇅ sort etc.
      toggle();
    });
    hd.addEventListener('keydown',e=>{
      if(e.key!=='Enter'&&e.key!==' ')return;
      if(isPassthrough(e.target))return;
      e.preventDefault();                  // Space must not scroll the page
      toggle();
    });
  });
}
function expandSection(id){
  const sec=document.getElementById(id);
  if(!sec)return;
  sec.classList.remove('collapsed');
  const hd=sec.querySelector('.sec-hd');
  if(hd)hd.setAttribute('aria-expanded','true');
}
