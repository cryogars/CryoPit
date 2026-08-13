// §7 per-layer density toggle. Reveals/hides the density columns; hiding is a
// guard against accidentally typing interval densities into layers, so it
// CLEARS values with a confirm. Loading a pit/draft with values flips it on.
//
// All or nothing: the toggle shows ρA, ρB and a computed average together.
// Layer density is a genuinely separate measurement from the §5 interval
// profile — which is the whole reason it lives behind a toggle rather than in
// §5 — so it gets the same duplicate-reading treatment, or none.
let _layerDensityOn=false;
function setLayerDensity(on){
  if(on===_layerDensityOn){_syncLayerDensityUI();return;}
  _layerDensityOn=!!on;
  const body=document.getElementById('sb');
  const rows=[...body.querySelectorAll('tr')];
  rows.forEach(tr=>{
    if(_layerDensityOn){
      const mk=(cls,html)=>{const td=document.createElement('td');td.className=cls;td.innerHTML=html;return td;};
      const at=tr.children[8];
      tr.insertBefore(mk('s-den','<input type="number" placeholder="'+examplePlaceholder('250')+'" style="min-width:52px" oninput="calcLayerAvg(this)">'),at);
      tr.insertBefore(mk('s-den','<input type="number" placeholder="'+examplePlaceholder('252')+'" style="min-width:52px" oninput="calcLayerAvg(this)">'),at);
      tr.insertBefore(mk('s-den s-den-avg','<input type="number" readonly tabindex="-1" style="min-width:52px" title="mean of ρA and ρB">'),at);
    }else{
      tr.querySelectorAll('.s-den').forEach(td=>td.remove());
    }
  });
  _syncLayerDensityUI(); tick();
}
// Mean of whatever readings are present, mirroring §5's rule: a 0 is not a
// density, so it never drags the mean down.
function calcLayerAvg(el){
  const tr=el.closest('tr'); if(!tr)return;
  const cells=[...tr.querySelectorAll('.s-den input')];
  const v=[cells[0],cells[1]].map(i=>num(i&&i.value)).filter(x=>x!==null&&x>0);
  if(cells[2])cells[2].value=v.length?Math.round(v.reduce((a,b)=>a+b)/v.length):'';
  if(typeof densityWarnings==='function')densityWarnings();
  tick();
}
function _syncLayerDensityUI(){
  document.querySelectorAll('.s-den-th').forEach(th=>{th.style.display=_layerDensityOn?'':'none';});
  const btn=document.getElementById('ld-toggle');
  if(btn){btn.classList.toggle('on',_layerDensityOn);
    btn.textContent=_layerDensityOn?'− ρ per layer':'+ ρ per layer';}
}
function toggleLayerDensity(){
  if(_layerDensityOn){
    const any=[...document.querySelectorAll('#sb .s-den input')].some(i=>i.value.trim());
    if(any&&!confirm('Hide per-layer density? Entered layer densities will be cleared.'))return;
  }
  setLayerDensity(!_layerDensityOn);
}

// Validation: inverted intervals are always typos and would corrupt
// gap-filling and SWE — block them with a named message.
// Physical-bounds validation (client mirror of the server checks):
// densities must be positive and cannot exceed ice (917 kg/m3); depths
// cannot be negative or exceed the total depth.
function _physicalBounds(){
  const errs=[];
  const HS=num(document.getElementById('depth').value);
  [...document.querySelectorAll('#db tr')].forEach((tr,i)=>{
    const ins=tr.querySelectorAll('input[type=number]');
    [['A',2],['B',3],['Extra',4]].forEach(([lbl,ix])=>{
      const v=num(ins[ix]?.value);
      if(v!==null&&!(v>0&&v<=917))errs.push(`Density interval ${i+1} profile ${lbl}: ${v} kg/m³ is outside 1–917 (ice)`);
    });
  });
  if(_layerDensityOn)[...document.querySelectorAll('#sb .s-den:not(.s-den-avg) input')].forEach((inp,i)=>{
    const v=num(inp.value);
    if(v!==null&&!(v>0&&v<=917))errs.push(`Layer ${i+1} density: ${v} kg/m³ is outside 1–917 (ice)`);
  });
  [['db','Density'],['lb','LWC'],['sb','Stratigraphy']].forEach(([id,lbl])=>{
    [...document.querySelectorAll('#'+id+' tr')].forEach((tr,i)=>{
      const ins=tr.querySelectorAll('input');
      const t=num(ins[0]?.value),b=num(ins[1]?.value);
      if((t!==null&&t<0)||(b!==null&&b<0))errs.push(`${lbl} interval ${i+1}: depths cannot be negative`);
      if(HS&&t!==null&&t>HS+0.51)errs.push(`${lbl} interval ${i+1}: top (${t}) exceeds total depth (${HS})`);
    });
  });
  [...document.querySelectorAll('#tb tr')].forEach((tr,i)=>{
    const h=num(tr.querySelector('input')?.value);
    // GROUND_MIN, not 0: a single reading below the snow-ground interface is a
    // real measurement and must not block the render.
    if(h!==null&&(h<GROUND_MIN||(HS&&h>HS+0.51)))
      errs.push(`Temperature row ${i+1}: height (${h}) outside ${GROUND_MIN} to ${HS||'?'} cm`);
  });
  // permittivity: snow sits above vacuum (1) and far below water (88);
  // >12 is beyond even saturated slush — treat outside (1, 12] as a typo
  [...document.querySelectorAll('#lb tr')].forEach((tr,i)=>{
    const ins=tr.querySelectorAll('input[type=number]');
    [['A',2],['B',3]].forEach(([lbl,ix])=>{
      const v=num(ins[ix]?.value);
      if(v!==null&&!(v>1&&v<=12))errs.push(`LWC interval ${i+1} profile ${lbl}: permittivity ${v} is outside (1, 12]`);
    });
  });
  return errs;
}

// Live field marking + §5 blocking lines: hard violations shouldn't wait for
// an Archive click to become visible. Runs on every rail redraw.
function liveBoundsMark(){
  const mark=(inp,bad)=>{if(inp)inp.classList.toggle('inp-bad',!!bad);};
  const hard=[];
  [...document.querySelectorAll('#db tr')].forEach((tr,i)=>{
    const ins=tr.querySelectorAll('input[type=number]');
    [['A',2],['B',3],['Extra',4]].forEach(([lbl,ix])=>{
      const v=num(ins[ix]?.value);
      const bad=v!==null&&!(v>0&&v<=917);
      mark(ins[ix],bad);
      if(bad)hard.push(`density interval ${i+1} ${lbl}: ${v} — must be 1–917 kg/m³ (ice)`);
    });
  });
  [...document.querySelectorAll('#sb .s-den input')].forEach((inp,i)=>{
    const v=num(inp.value);mark(inp,v!==null&&!(v>0&&v<=917));
  });
  [...document.querySelectorAll('#lb tr')].forEach(tr=>{
    const ins=tr.querySelectorAll('input[type=number]');
    [2,3].forEach(ix=>{const v=num(ins[ix]?.value);mark(ins[ix],v!==null&&!(v>1&&v<=12));});
  });
  // Interval geometry: red cells the moment a top/bottom goes impossible.
  //
  // All three tables were being MARKED red, but only density pushed a message
  // — so an inverted stratigraphy layer reddened its inputs while §7's header
  // glyph stayed ✓ and the checklist stayed green, and the only real symptom
  // was Archive refusing at the end. The server has always rejected these
  // (repository.py: "Stratigraphy interval 1: top (120) must be greater than
  // bottom (130)"), so each section now reports its own blockers in its own
  // box, using the server's wording.
  const HS=num(document.getElementById('depth').value);
  const bySection={db:hard,lb:[],sb:[]};
  [['db','Density interval'],['lb','LWC interval'],['sb','Stratigraphy interval']].forEach(([id,lbl])=>{
    [...document.querySelectorAll('#'+id+' tr')].forEach((tr,i)=>{
      const ins=tr.querySelectorAll('input');
      const t=num(ins[0]?.value),b=num(ins[1]?.value);
      const badT=(t!==null&&(t<0||(HS&&t>HS+0.51)))||(t!==null&&b!==null&&t<=b);
      const badB=(b!==null&&b<0)||(t!==null&&b!==null&&t<=b);
      mark(ins[0],badT);mark(ins[1],badB);
      const out=bySection[id];
      if(t!==null&&b!==null&&t<=b)out.push(`${lbl} ${i+1}: top (${t}) must be greater than bottom (${b})`);
      if(t!==null&&HS&&t>HS+0.51)out.push(`${lbl} ${i+1}: top (${t}) exceeds total depth (${HS})`);
      if((t!==null&&t<0)||(b!==null&&b<0))out.push(`${lbl} ${i+1}: depths cannot be negative`);
    });
  });
  liveBoundsMark._bySection=bySection;
  [...document.querySelectorAll('#tb tr')].forEach(tr=>{
    const ins=tr.querySelectorAll('input');
    const h=num(ins[0]?.value), t=num(ins[1]?.value);
    // -50..HS, not 0..HS: see the ground-reading note in densityWarnings()
    mark(ins[0],h!==null&&(h<GROUND_MIN||(HS&&h>HS+0.51)));
    // The READING cell was never marked at all. Every other table points at the
    // offending cell — a density outside 1–917, an inverted interval — but a
    // temperature of -60 blocked the archive while naming only the row number,
    // and -30 warned with nothing on screen to look at. In a forty-row profile
    // that is a hunt. Blocking (below -40) is red like the rest; the softer
    // ones (above freezing, or colder than -25) get the amber ring.
    mark(ins[1],t!==null&&t<-40);
    if(ins[1])ins[1].classList.toggle('inp-warn',
      t!==null&&t>=-40&&(t>0||t<-25));
  });
  return hard;
}

// Soft §5 warnings (non-blocking; export auto-resolves or the numbers may be
// legitimate): overlapping intervals, big A/B/Extra disagreement, unusual
// but possible values.
const GROUND_MIN=-10;   // mirrors repository.GROUND_PROBE_MIN_CM
// "Started but not finished" — advisory only, and deliberately NOT counted
// as an error: a row being typed is not a mistake. Reported once per table
// rather than once per row, so a table half-filled does not produce a wall
// of near-identical amber lines.
function halfTyped(bodyId,firstIx,needIx,what){
  return _halfTyped(bodyId,firstIx,needIx,what);}
const _halfTyped=(bodyId,firstIx,needIx,what)=>{
    // firstIx null => "started" is ANY filled cell in the row, so a row can be
    // reported for a gap anywhere, not only when its first cell is filled.
    const rows=[...document.querySelectorAll('#'+bodyId+' tr')].map((tr,i)=>({tr,i})).filter(({tr})=>{
      const ins=tr.querySelectorAll('input:not([readonly]),select');
      const started=(firstIx===null)
        ? [...ins].some(el=>String(el.value).trim()!=='')
        : ins[firstIx]&&String(ins[firstIx].value).trim()!=='';
      const done=needIx.some(ix=>ins[ix]&&String(ins[ix].value).trim()!=='');
      return started&&!done;
    });
    if(!rows.length)return[];
    // Name the rows. "2 rows started but…" left you hunting; the numbers match
    // the row order on screen, and each row is marked in its left gutter.
    rows.forEach(({tr})=>{
      tr.classList.add('row-warn');
      // the gutter glyph is a CSS ::before and cannot carry a tooltip itself,
      // so the explanation goes on the row
      tr.title=`Started but ${what}`;
    });
    const nums=rows.map(({i})=>i+1);
    const list=nums.length>4
      ? `${nums.length} rows (${nums.slice(0,4).join(', ')}…)`
      : `row${nums.length>1?'s':''} ${nums.join(', ')}`;
    return [`${list} started but ${what}`];
  };

function densityWarnings(){
  document.querySelectorAll('#db tr,#lb tr,#sb tr,#tb tr,#ssab tr').forEach(tr=>{
    tr.classList.remove('row-warn');
    // clear the tooltip too, or a row that has since been completed keeps
    // claiming it is unfinished on hover
    if(tr.title)tr.removeAttribute('title');
  });
  const w=[];
  const rows=[...document.querySelectorAll('#db tr')].map((tr,i)=>{
    const ins=tr.querySelectorAll('input[type=number]');
    return{i:i+1,top:num(ins[0]?.value),bottom:num(ins[1]?.value),
      v:[num(ins[2]?.value),num(ins[3]?.value),num(ins[4]?.value)].filter(x=>x!==null&&x>0)};
  }).filter(r=>r.top!==null&&r.bottom!==null&&r.top>r.bottom);
  const sorted=[...rows].sort((a,b)=>b.top-a.top);
  for(let k=1;k<sorted.length;k++){
    if(sorted[k].top>sorted[k-1].bottom+0.001)
      w.push(`intervals ${sorted[k-1].i} & ${sorted[k].i} overlap — export clips (upper wins)`);
  }
  rows.forEach(r=>{
    if(r.v.length>=2){
      const mean=r.v.reduce((a,b)=>a+b)/r.v.length,half=(Math.max(...r.v)-Math.min(...r.v))/2;
      if(half>0.25*mean)w.push(`interval ${r.i}: profiles disagree by ±${Math.round(half)} (>25% of mean) — check a typo?`);
    }
    r.v.forEach(v=>{if(v<50||v>700)w.push(`interval ${r.i}: ${v} kg/m³ is unusual for snow (typical 50–700; ice layers can reach 917)`);});
  });
  // §7 layer densities get the same unusual-value screening, shown locally
  const lw=[];
  if(_layerDensityOn)[...document.querySelectorAll('#sb .s-den:not(.s-den-avg) input')].forEach((inp,i)=>{
    const v=num(inp.value);
    if(v!==null&&v>0&&v<=917&&(v<50||v>700))
      lw.push(`layer ${Math.floor(i/2)+1} ${i%2?'ρB':'ρA'}: ${v} kg/m³ is unusual for snow (typical 50–700; ice layers can reach 917)`);
  });
  // §4 had NO live validation at all — density, LWC and stratigraphy each had
  // a warning box and temperature did not, so a height outside the pit was
  // accepted silently on screen and only rejected when Archive was pressed.
  // Mirrors repository.py's rule exactly, including the wording, so the live
  // message and the refusal cannot disagree.
  // populate liveBoundsMark._bySection before any box reads it
  if(typeof liveBoundsMark==='function')liveBoundsMark();
  // Every time warning is rendered in the section that owns the field, so §1
  // shows only Pit open. §4 appends its two to the temperature box below, and
  // §8 has its own. See timeWarnings() in 20_tables.js.
  const tmEl=document.getElementById('time-warn');
  if(tmEl){
    const tw=timeWarnings('time-warn');
    tmEl.innerHTML=tw.map(x=>`<span class="warn-soft">⚠ ${esc(x)}</span>`).join('<br>');
    tmEl.className='warn-box'+(tw.length?' has-warn':'');
    tmEl.style.display=tw.length?'':'none';
  }
  // §8 has no other live validation, so this box exists for the times alone.
  const saEl=document.getElementById('ssa-warn');
  if(saEl){
    const sw=timeWarnings('ssa-warn');
    saEl.innerHTML=sw.map(x=>`<span class="warn-soft">⚠ ${esc(x)}</span>`).join('<br>');
    saEl.className='warn-box'+(sw.length?' has-warn':'');
    saEl.style.display=sw.length?'':'none';
  }
  const tEl=document.getElementById('t-warn');
  if(tEl){
    const hs=num(gv('depth'));
    const th=[],ts=[];
    [...document.querySelectorAll('#tb tr')].forEach((tr,i)=>{
      const ins=tr.querySelectorAll('input');
      const h=num(ins[0]?.value), t=num(ins[1]?.value);
      // A NEGATIVE height is legitimate here and nowhere else: the profile runs
      // down the pack (40, 30, 20, 10, 0) and a crew may take one ground
      // reading below the snow-ground interface. Mirrors GROUND_PROBE_MIN_CM.
      if(h!==null&&(h<GROUND_MIN||(hs&&h>hs+0.51)))
        th.push(`row ${i+1}: height (${h}) outside ${GROUND_MIN}–${hs||'?'} cm`);
      else if(h!==null&&h<0)
        ts.push(`row ${i+1}: height ${h} cm is below the snow, so it is recorded as a soil temperature (marked in the CSV header)`);
      // +1 rather than 0: wet snow sits AT zero and instruments read either
      // side of it, so warning above freezing fired on correct readings.
      if(t!==null&&t>1)
        ts.push(`row ${i+1}: ${t} °C is well above freezing. Check the reading.`);
      if(t!==null&&t<-40)
        th.push(`row ${i+1}: ${t} °C is below any plausible snow temperature`);
      else if(t!==null&&t<-25)
        ts.push(`row ${i+1}: ${t} °C is extreme. Check the reading.`);
    });
    // COVERAGE — the temperature analogue of §7's "layers do not reach the
    // ground". §7 can demand an exact partition of the pack; a temperature
    // profile is a set of point readings, so the question is only whether the
    // readings span it.
    //
    // The thresholds are the app's OWN generator, not a guess: autofillTemp()
    // builds [HS, …snapped intervals…, 0], starting exactly at the surface and
    // ending exactly at the ground. So a profile is covered when its lowest
    // reading reaches 0 (or below — a negative height IS a ground reading) and
    // its highest reaches HS. Anything short of that is a real gap: no basal
    // temperature at one end, no surface temperature at the other.
    //
    // An earlier version cleared one sampling step above the ground, which let
    // a profile stopping at 10 cm count as finished even though the basal
    // reading — the one that says whether the pack is isothermal at its base —
    // was missing. Soft only: it never blocks the archive.
    const hts=[...document.querySelectorAll('#tb tr')]
      .map(tr=>num(tr.querySelectorAll('input')[0]?.value))
      .filter(h=>h!==null);
    if(hs&&hts.length){
      const lo=Math.min(...hts), hi=Math.max(...hts);
      if(lo>0.51)      ts.push(`readings stop at ${lo} cm, not the ground surface`);
      if(hs-hi>0.51)   ts.push(`readings start at ${hi} cm, below the ${hs} cm surface`);
    }
    // Aggregated, like every other table: ten half-typed rows produced ten
    // near-identical amber lines here while §5, §6 and §7 reported one.
    const hard=th, soft=[...ts,...halfTyped('tb',0,[1],'no temperature entered')];
    // §4 owns Profile start/end, so their small-hours warnings belong here
    // rather than under Identity. They are NOT prefixed "Temperature" — they
    // are about the clock, not about a reading.
    const timeW=timeWarnings('t-warn');
    const tl=[...hard.map(x=>`<span class="warn-block">✖ Blocks archive: Temperature ${esc(x)}</span>`),
              ...soft.map(x=>`<span class="warn-soft">⚠ Temperature ${esc(x)}</span>`),
              ...timeW.map(x=>`<span class="warn-soft">⚠ ${esc(x)}</span>`)];
    tEl.innerHTML=tl.join('<br>');
    tEl.className='warn-box'+(hard.length?' has-block'
                              :((soft.length||timeW.length)?' has-warn':''));
    tEl.style.display=tl.length?'':'none';
  }

  // LWC readings come from either instrument and the form does not record
  // which, so the contradiction is reported rather than resolved.
  const lEl=document.getElementById('l-warn');
  if(lEl){
    const rows=[...document.querySelectorAll('#lb tr')]
      .filter(tr=>{const i=tr.querySelectorAll('input');return i[0]&&i[0].value.trim()!==''});
    const declared=(typeof _checklistYes==='function')&&
      (_checklistYes('Digital LWC')||_checklistYes('Lyte Probe'));
    const lHard=(liveBoundsMark._bySection&&liveBoundsMark._bySection.lb)||[];
    const show=rows.length&&!declared;
    const ll=[...lHard.map(x=>`<span class="warn-block">✖ Blocks archive: ${esc(x)}</span>`)];
    if(show)ll.push('<span class="warn-soft">⚠ LWC measurements recorded, but no LWC instrument is marked Used in §9.</span>');
    halfTyped('lb',0,[2,3],'no permittivity entered').forEach(x=>ll.push(`<span class="warn-soft">⚠ ${esc(x)}</span>`));
    lEl.innerHTML=ll.join('<br>');
    lEl.className='warn-box'+(lHard.length?' has-block':(ll.length?' has-warn':''));
    lEl.style.display=ll.length?'':'none';
  }
  const sEl=document.getElementById('s-warn');
  if(sEl){
    const sHard=(liveBoundsMark._bySection&&liveBoundsMark._bySection.sb)||[];
    // §7's dropdowns always carry a value, so "no grain type" cannot be
    // detected — a layer is counted as started once it has a top and no bottom.
    // A stratigraphy profile describes the WHOLE pack, so it is not finished
    // until the layers reach the ground. Without this, entering 112-96 of a
    // 130 cm pit already showed the section complete.
    // The top end was never checked, so a profile that began below the surface
    // — the first layer logged as 96-74 in a 120 cm pit — read as complete.
    const gap=_coverage('sb','layers');
    const lw2=[...lw,...gap,...halfTyped('sb',0,[1],'no bottom depth entered')];
    const sl=[...sHard.map(x=>`<span class="warn-block">✖ Blocks archive: ${esc(x)}</span>`),
              ...lw2.map(x=>`<span class="warn-soft">⚠ ${esc(x)}</span>`)];
    sEl.innerHTML=sl.join('<br>');
    sEl.className='warn-box'+(sHard.length?' has-block':(lw2.length?' has-warn':''));
    sEl.style.display=sl.length?'':'none';
    if(typeof refreshStatusGlyphs==='function')refreshStatusGlyphs();
  }
  const hard=(typeof liveBoundsMark==='function')?liveBoundsMark():[];
  const el=document.getElementById('d-warn');
  if(el){
    // A blocker and a soft warning are not the same thing and must not look
    // the same: one stops the archive, the other is advisory. They shared the
    // amber .soft-warn styling, so "Blocks archive: top (120) exceeds total
    // depth (100)" read like a suggestion.
    // "Started" means any cell in the row is filled, and "finished" means the
    // interval AND at least one reading. Testing only cell 0 missed a row where
    // someone typed a top and the readings but skipped the bottom — nothing was
    // reported at all, because that row failed neither test.
    const w2=[...w,
      ...halfTyped('db',null,[0,1],'no interval entered'),
      ...halfTyped('db',null,[2,3,4],'no density reading entered'),
      ..._coverage('db','intervals')];
    const lines=[
      ...hard.map(x=>`<span class="warn-block">✖ Blocks archive: ${esc(x)}</span>`),
      ...w2.map(x=>`<span class="warn-soft">⚠ ${esc(x)}</span>`)];
    el.innerHTML=lines.join('<br>');
    el.className='warn-box'+(hard.length?' has-block':(w2.length?' has-warn':''));
    el.style.display=lines.length?'':'none';
    // the box just changed; the header glyph must not wait for the next tick()
    if(typeof refreshStatusGlyphs==='function')refreshStatusGlyphs();
  }
  return w;
}

// Does an interval profile span the pack?
//
// The same question in §5 and §7: both describe the pack from the snow surface
// down to the ground, so intervals that start below the surface or stop above
// it have left part of the pack undescribed. §4 asks the same thing of point
// readings. Soft only — it never blocks the archive.
function _coverage(bodyId,noun){
  const hs=num(gv('depth'));
  const rows=[...document.querySelectorAll('#'+bodyId+' tr')]
    .map(tr=>tr.querySelectorAll('input'))
    .map(ins=>({t:num(ins[0]?.value),b:num(ins[1]?.value)}))
    .filter(r=>r.t!==null&&r.b!==null);
  if(!rows.length)return[];
  const lowest=Math.min(...rows.map(r=>r.b)), highest=Math.max(...rows.map(r=>r.t));
  const out=[];
  if(lowest>0.51)
    out.push(`${noun} stop at ${lowest} cm, not the ground surface`);
  if(hs&&hs-highest>0.51)
    out.push(`${noun} start at ${highest} cm, below the ${hs} cm surface`);
  return out;
}

function _invertedIntervals(){
  const errs=[];
  [['db','Density'],['lb','LWC'],['sb','Stratigraphy']].forEach(([id,lbl])=>{
    [...document.querySelectorAll('#'+id+' tr')].forEach((tr,i)=>{
      const ins=tr.querySelectorAll('input');
      const t=num(ins[0]?.value),b=num(ins[1]?.value);
      if(t!==null&&b!==null&&t<=b)errs.push(`${lbl} interval ${i+1}: top (${t}) must be greater than bottom (${b})`);
    });
  });
  return errs;
}

