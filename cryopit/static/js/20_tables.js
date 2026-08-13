const TABLE_ENTER_BODIES=new Set(['tb','db','lb','sb']);
let _tableEnterNavReady=false;

function _isTableEnterInput(el){
  if(!el||String(el.tagName||'').toUpperCase()!=='INPUT')return false;
  const type=String(el.type||'text').toLowerCase();
  return !el.disabled&&!el.readOnly&&!['checkbox','radio','file','button','submit','reset','hidden'].includes(type);
}

function handleTableEnter(e){
  if(e.key!=='Enter'||e.shiftKey||e.ctrlKey||e.altKey||e.metaKey||e.isComposing)return;
  const input=e.target;
  if(!_isTableEnterInput(input))return;
  const body=input.closest&&input.closest('tbody');
  if(!body||!TABLE_ENTER_BODIES.has(body.id))return;

  // Enter is navigation inside these tables, never implicit form submission.
  // Row creation stays explicit through each section's existing Add control.
  e.preventDefault();
  const row=input.closest('tr'),cell=input.closest('td');
  if(!row||!cell)return;
  const rows=[...(body.rows||[])];
  const next=rows[rows.indexOf(row)+1];
  if(!next)return;  // final existing row: stay put
  const nextCell=next.cells&&next.cells[cell.cellIndex];
  if(!nextCell)return;
  const target=[...nextCell.querySelectorAll('input')].find(_isTableEnterInput);
  if(target)target.focus();
}

function initTableEnterNavigation(){
  if(_tableEnterNavReady)return;
  document.addEventListener('keydown',handleTableEnter);
  _tableEnterNavReady=true;
}

function addRow(t,focus){
  const map={t:'tb',d:'db',l:'lb',s:'sb',sa:'ssab'};
  const tr=document.createElement('tr');
  if(t==='t'){
    tr.innerHTML=`<td><input type="number" placeholder="${examplePlaceholder('100')}"></td>
      <td><input type="number" step="0.1" placeholder="${examplePlaceholder('-2.0')}"></td>
      <td><button class="del" onclick="this.closest('tr').remove();cnt('t')">×</button></td>`;
  } else if(t==='d'){
    tr.innerHTML=`<td><input type="number" placeholder="${examplePlaceholder('120')}"></td>
      <td><input type="number" placeholder="${examplePlaceholder('110')}"></td>
      <td><input type="number" oninput="calcAvg(this.closest('tr'))"></td>
      <td><input type="number" oninput="calcAvg(this.closest('tr'))"></td>
      <td><input type="number" oninput="calcAvg(this.closest('tr'))"></td>
      <td class="avg"><input readonly placeholder="—" tabindex="-1"></td>
      <td><button class="del" onclick="this.closest('tr').remove();cnt('d')">×</button></td>`;
  } else if(t==='l'){
    tr.innerHTML=`<td><input type="number" placeholder="${examplePlaceholder('120')}"></td>
      <td><input type="number" placeholder="${examplePlaceholder('110')}"></td>
      <td><input type="number" step="0.001" placeholder="${examplePlaceholder('1.173')}"></td>
      <td><input type="number" step="0.001" placeholder="—"></td>
      <td><button class="del" onclick="this.closest('tr').remove();cnt('l')">×</button></td>`;
  } else if(t==='s'){
    // Top/Bottom/G columns tightened (54/56px) so digits stay fully visible
    // while leaving room for the optional per-layer density column.
    // three cells or none — setLayerDensity() inserts the same trio when the
    // toggle is flipped, and a row built while it is already on must match
    const dcell=_layerDensityOn
      ? `<td class="s-den"><input type="number" placeholder="${examplePlaceholder('250')}" style="min-width:52px" oninput="calcLayerAvg(this)"></td>`
      + `<td class="s-den"><input type="number" placeholder="${examplePlaceholder('252')}" style="min-width:52px" oninput="calcLayerAvg(this)"></td>`
      + `<td class="s-den s-den-avg"><input type="number" readonly tabindex="-1" style="min-width:52px" title="mean of ρA and ρB"></td>`
      : '';
    tr.innerHTML=`<td><input type="number" placeholder="${examplePlaceholder('120')}" style="min-width:54px"></td>
      <td><input type="number" placeholder="${examplePlaceholder('110')}" style="min-width:54px"></td>
      <td><input type="number" step="0.1" placeholder="${examplePlaceholder('0.5')}" style="min-width:56px"></td>
      <td><input type="number" step="0.1" placeholder="${examplePlaceholder('1.0')}" style="min-width:56px"></td>
      <td><input type="number" step="0.1" placeholder="${examplePlaceholder('0.7')}" style="min-width:56px"></td>
      <td><select style="min-width:60px">${so(G)}</select></td>
      <td><select style="min-width:52px">${so(H)}</select></td>
      <td><select style="min-width:50px">${so(W)}</select></td>
      ${dcell}
      <td><input type="text" placeholder="notes…" style="min-width:90px"></td>
      <td class="s-cam"><button class="cam" type="button" onclick="pickLayerPhotos(this)"
        title="Attach photos of this layer" aria-label="Attach photos of this layer" disabled>📷<span class="cam-n"></span></button>
        <button class="lp-toggle" type="button" onclick="toggleLayerPhotos(this)" title="Show this layer's photographs"
        aria-label="Show this layer's photographs" style="display:none">▾</button></td>
      <td><button class="del" onclick="this.closest('tr').remove();cnt('s')">×</button></td>`;
  } else if(t==='sa'){
    tr.innerHTML=`<td><input type="number" placeholder="${examplePlaceholder('35')}"></td>
      <td><input type="number" step="0.001" placeholder="${examplePlaceholder('1.147')}"></td>
      <td><input type="number" step="0.01" placeholder="${examplePlaceholder('36.22')}"></td>
      <td><input type="number" step="0.01" placeholder="${examplePlaceholder('23.76')}"></td>
      <td><select style="min-width:60px">${so(G)}</select></td>
      <td><input type="text" placeholder="notes…" style="min-width:80px"></td>
      <td><button class="del" onclick="this.closest('tr').remove();cnt('sa')">×</button></td>`;
  }
  document.getElementById(map[t]).appendChild(tr);
  cnt(t); tick();
  if(focus!==false)tr.querySelector('input,select').focus();
  return tr;
}

// Auto-fill helpers ------------------------------------------------
function _hs(){ return num(gv('depth'))||0; }   // total snow height (HS)

// Temperature: start at HS, snap to nearest interval boundary below, step to 0.
// e.g. HS=83, step=10 -> 83,80,70,...,0 ; step=5 -> 83,80,75,...,0
function autofillTemp(){
  const hs=_hs(), step=parseInt(gv('t-interval'))||10;
  if(!hs){setst('set Total depth (§1) first','err');return;}
  const depths=[hs];
  let d=Math.floor(hs/step)*step;          // snap down to interval boundary
  if(d===hs) d-=step;                       // if HS already on boundary, next one down
  for(; d>0; d-=step) depths.push(d);
  depths.push(0);
  document.getElementById('tb').innerHTML='';
  depths.forEach(h=>{
    const tr=document.createElement('tr');
    tr.innerHTML=`<td><input type="number" value="${h}"></td>
      <td><input type="number" step="0.1" placeholder="${examplePlaceholder('-2.0')}"></td>
      <td><button class="del" onclick="this.closest('tr').remove();cnt('t')">×</button></td>`;
    document.getElementById('tb').appendChild(tr);
  });
  cnt('t'); tick();
}

// Density: fixed intervals from HS downward. e.g. HS=87,step=10 -> 87-77,77-67,...
function autofillDensity(){
  const hs=_hs(), step=parseInt(gv('d-interval'))||10;
  if(!hs){setst('set Total depth (§1) first','err');return;}
  const rows=[];
  for(let top=hs; top>0; top-=step) rows.push([top, Math.max(top-step,0)]);
  document.getElementById('db').innerHTML='';
  rows.forEach(([top,bot])=>{
    const tr=document.createElement('tr');
    tr.innerHTML=`<td><input type="number" value="${top}"></td>
      <td><input type="number" value="${bot}"></td>
      <td><input type="number" oninput="calcAvg(this.closest('tr'))"></td>
      <td><input type="number" oninput="calcAvg(this.closest('tr'))"></td>
      <td><input type="number" oninput="calcAvg(this.closest('tr'))"></td>
      <td class="avg"><input readonly placeholder="—" tabindex="-1"></td>
      <td><button class="del" onclick="this.closest('tr').remove();cnt('d')">×</button></td>`;
    document.getElementById('db').appendChild(tr);
  });
  cnt('d'); tick();
}

// LWC: copy the top/bottom interval pairs from density, leaving permittivity blank.
function copyDensityIntervals(){
  const drows=document.querySelectorAll('#db tr');
  if(!drows.length){setst('add density intervals first','err');return;}
  document.getElementById('lb').innerHTML='';
  drows.forEach(dtr=>{
    const di=dtr.querySelectorAll('input');
    const top=di[0].value, bot=di[1].value;
    const tr=document.createElement('tr');
    tr.innerHTML=`<td><input type="number" value="${top}"></td>
      <td><input type="number" value="${bot}"></td>
      <td><input type="number" step="0.001" placeholder="${examplePlaceholder('1.173')}"></td>
      <td><input type="number" step="0.001" placeholder="—"></td>
      <td><button class="del" onclick="this.closest('tr').remove();cnt('l')">×</button></td>`;
    document.getElementById('lb').appendChild(tr);
  });
  cnt('l'); tick();
}

function calcAvg(tr){
  // only POSITIVE values are densities — a 0 is physically impossible and
  // must never drag the average down (validation blocks it at export too)
  const ins=tr.querySelectorAll('input[type=number]');
  const v=[ins[2],ins[3],ins[4]].map(i=>num(i.value)).filter(x=>x!==null&&x>0);
  tr.querySelector('.avg input').value=v.length?Math.round(v.reduce((a,b)=>a+b)/v.length):'';
}

function cnt(t){
  const map={t:['tb','tc-cnt','measurements'],d:['db','dc-cnt','intervals'],
             l:['lb','lc-cnt','intervals'],s:['sb','sc-cnt','layers'],sa:['ssab','sa-cnt','measurements']};
  const [bid,cid,lbl]=map[t];
  const n=document.getElementById(bid).children.length;
  document.getElementById(cid).textContent=`${n} ${lbl}`;
  tick();
}

function milCheck(inp){
  // red alone isn't an explanation — a short local note says what's expected
  const v=inp.value,bad=v.length===4&&!goodTime(v);
  inp.style.color=bad?'var(--red)':'var(--ink)';
  let note=inp.nextElementSibling;
  if(!(note&&note.classList&&note.classList.contains('err-note'))){
    note=document.createElement('span');note.className='err-note';
    inp.after(note);
  }
  note.textContent=bad?'24-h HHMM, e.g. 1430':'';
  // Recompute the warning boxes here, in the handler EVERY time field shares.
  // Only Pit open used to do this, from its own inline oninput; §4's and §8's
  // times called milCheck alone. So a small-hours time typed in §4 showed
  // nothing until some unrelated edit happened to trigger a repaint, and
  // correcting it left the stale warning on screen for the same reason.
  if(typeof densityWarnings==='function')densityWarnings();
}
function goodTime(v){
  return /^\d{4}$/.test(v)&&parseInt(v.slice(0,2))<=23&&parseInt(v.slice(2))<=59;
}

// EVERY HHMM field in the app, declared once.
//
// There are two separate checks on times: 60_api.js blocks the archive on a
// malformed one, and 80_layer_density.js warns about the small hours. Each kept
// its own hand-written list, and they had already drifted — the SSA calibration
// time was in the blocking list but not the warning one, so a mistyped 0800 as
// 0100 in §8 was silently accepted while the identical slip in §1 was flagged.
// Both now read this, so a new time field cannot be added to one and forgotten
// in the other.
// Each field also names the warning box it belongs in. A time warning is shown
// in the section that owns the field — §4's times warn inside §4 — so the
// message does not need to carry a section reference to be findable. The
// section IS still recorded, because the ARCHIVE blocker is a single global
// list where "Profile start" alone would be ambiguous.
//
//        id              label              section    warning box
const TIME_FIELDS=[
  ['po',           'Pit open',             '\u00a71', 'time-warn'],
  ['ts',           'Profile start',        '\u00a74', 't-warn'   ],
  ['te',           'Profile end',          '\u00a74', 't-warn'   ],
  ['ssa-cal-time', 'Calibration time',     '\u00a78', 'ssa-warn' ],
];

// Small-hours check for the fields belonging to ONE warning box.
// HHMM has no separator and the common slip is a 12-hour-clock reflex: 1:13 in
// the afternoon goes in as 0113. Flag the small hours rather than forbid them —
// pits ARE dug at night — and name the time that was probably meant, computed
// from what was actually typed rather than a fixed example.
function timeWarnings(boxId){
  const out=[];
  TIME_FIELDS.forEach(([id,lbl,sec,box])=>{
    if(box!==boxId)return;
    const el=document.getElementById(id); if(!el)return;
    const v=(el.value||'').replace(':','').trim();
    if(!/^\d{4}$/.test(v))return;
    const hh=+v.slice(0,2), mm=v.slice(2);
    // WINDOW: 00:00–06:59.
    //
    // The error this catches is one-directional. Typing a PM time on a 12-hour
    // reflex produces an AM value — 17:30 becomes 0530 — so only the small
    // hours are ambiguous. An evening time entered as 1930 is already correct
    // 24-hour and needs no warning, which is why the window does not extend
    // into the night at the other end however late the crew works.
    //
    // It runs to 06:59 rather than 04:59 so that 0500–0659 is covered, and
    // those map back to 17:00–18:59 — among the most common times to have a
    // pit open. A genuine pre-dawn start is warned about and dismissed by
    // ignoring it; the warning never blocks.
    if(hh>=7)return;
    const pm=String(hh+12).padStart(2,'0')+':'+mm;
    out.push(`${lbl} reads ${v.slice(0,2)}:${mm}. Working before dawn, or should that be ${pm}?`);
  });
  return out;
}


// Sort a table's rows surface -> ground on demand (never automatic — rows
// must not jump while someone is typing). Exports/plots sort internally
// anyway; this just tidies the on-screen order, incl. late-inserted rows.
function sortRows(t){
  const bid={t:'tb',d:'db',l:'lb',s:'sb',sa:'ssab'}[t];   // 'sa' was missing: sortRows('sa') threw
  const body=document.getElementById(bid);
  if(!body)return;
  [...body.children]
    .sort((a,b)=>(num(b.querySelector('input')?.value)??-1e9)-(num(a.querySelector('input')?.value)??-1e9))
    .forEach(tr=>body.appendChild(tr));
  tick();
}

// Interval-board rows are three views of one measurement: SWE (mm) = density
// (kg m-3) x depth (cm) / 100, from water density 1000 kg m-3. Enter any two
// and the third follows, so nobody works it out on a wet notebook.
//
// The computed cell is marked .is-derived and can still be typed over — a crew
// may have a reading that disagrees slightly with the arithmetic, and the app
// should record what was measured rather than overwrite it. Typing into a
// derived cell simply makes it the source.
function ibSolve(row, edited){
  const el = k => document.getElementById('ib-'+k+'-'+row);
  const d=el('d'), sw=el('s'), rho=el('r');
  if(!d||!sw||!rho)return;
  // whichever field was just edited is authoritative
  [sw,rho,d].forEach(x=>{ if(x.dataset.derived==='1' && x!==el(edited)) {} });
  el(edited).dataset.derived='';
  const D=num(d.value), S=num(sw.value), R=num(rho.value);
  const set=(x,v)=>{ x.value=(v===null?'':Math.round(v*10)/10); x.dataset.derived=v===null?'':'1';
                     x.classList.toggle('is-derived',v!==null); };
  if(D!==null&&D>0){
    if(edited!=='s'&&R!==null&&R>0){ set(sw, R*D/100); }
    else if(edited!=='r'&&S!==null&&S>0){ set(rho, S*100/D); }
  }else if(S!==null&&S>0&&R!==null&&R>0&&edited!=='d'){
    set(d, S*100/R);
  }
  tick();
}
