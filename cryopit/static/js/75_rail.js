// Live core rail — miniature of the snowpack, redrawn as you type ------
let _miniT=null;
function scheduleMini(){clearTimeout(_miniT);_miniT=setTimeout(drawMini,300);}
function drawMini(){
  const el=document.getElementById('mini-core');
  if(!el)return;
  const p=collect();
  const HS=p.meta.total_depth||0;
  const strat=(p.stratigraphy||[]).filter(l=>l.top!=null&&l.bottom!=null);
  document.getElementById('mc-hs').textContent=HS?HS+' cm':'—';
  document.getElementById('mc-lay').textContent=strat.length;
  // Bulk density and SWE per the documented CryoPit density rules
  // (README "Density rules"): sort surface->ground, clip overlaps (upper
  // interval wins), per-interval mean of measured profiles, then vertical
  // gap filling — middle gap = mean of neighbours; edge gaps <=25% of HS
  // extend the edge interval; >25% fall back to the thickness-weighted mean.
  // Only DENSITY is inferred for gaps; every centimetre of depth is real.
  if(typeof densityWarnings==='function')densityWarnings();
  // rows that fail geometry bounds (negative bottoms, tops beyond HS) are
  // typos-in-progress: they block archive, so the live numbers must not use
  // them either
  const inBounds=d=>d.bottom>=0&&(!HS||d.top<=HS+0.51);
  let meas=(p.density||[])
    .filter(d=>d.top!=null&&d.bottom!=null&&d.top>d.bottom&&inBounds(d)&&[d.a,d.b,d.c].some(x=>x!=null&&x>0))
    .map(d=>{const v=[d.a,d.b,d.c].filter(x=>x!=null&&x>0);
      return{top:d.top,bottom:d.bottom,rho:v.reduce((a,b)=>a+b)/v.length};})
    .sort((a,b)=>b.top-a.top);
  // agreed fallback: interval densities (§5) always win; per-layer densities
  // (§7) step in ONLY when no interval densities exist at all
  let fromLayers=false;
  if(!meas.length){
    meas=(p.stratigraphy||[])
      .filter(l=>l.top!=null&&l.bottom!=null&&l.top>l.bottom&&inBounds(l)&&l.layer_density!=null&&l.layer_density>0)
      .map(l=>({top:l.top,bottom:l.bottom,rho:l.layer_density}))
      .sort((a,b)=>b.top-a.top);
    fromLayers=meas.length>0;
  }
  for(let i=1;i<meas.length;i++){                 // clip overlaps, upper wins
    if(meas[i].top>meas[i-1].bottom)meas[i].top=meas[i-1].bottom;
  }
  const dcol=meas.filter(x=>x.top>x.bottom);
  let sumRT=0,sumT=0;
  dcol.forEach(x=>{const t=x.top-x.bottom;sumRT+=x.rho*t;sumT+=t;});
  const wmean=sumT>0?sumRT/sumT:null;
  let swe_mm=null,bulk=null,filledCm=0;
  if(dcol.length&&HS){
    const maxEdge=0.25*HS,full=[];
    const gapTop=HS-dcol[0].top;
    if(gapTop>0.001){
      if(gapTop<=maxEdge){dcol[0].top=HS;filledCm+=gapTop;}   // extension applies density to unmeasured cm — counts as interp
      else{full.push({top:HS,bottom:dcol[0].top,rho:wmean});filledCm+=gapTop;}
    }
    for(let i=0;i<dcol.length;i++){
      full.push(dcol[i]);
      if(i+1<dcol.length){
        const gap=dcol[i].bottom-dcol[i+1].top;
        if(gap>0.001){full.push({top:dcol[i].bottom,bottom:dcol[i+1].top,
          rho:(dcol[i].rho+dcol[i+1].rho)/2});filledCm+=gap;}
      }
    }
    const last=dcol[dcol.length-1];
    if(last.bottom>0.001){
      // Both branches apply density to UNMEASURED centimetres, so both count
      // toward the "interp" label — "measured full depth" must mean exactly that.
      if(last.bottom<=maxEdge){filledCm+=last.bottom;last.bottom=0;}
      else{full.push({top:last.bottom,bottom:0,rho:wmean});filledCm+=last.bottom;}
    }
    let sRT=0;full.forEach(x=>sRT+=x.rho*(x.top-x.bottom));
    swe_mm=sRT/100;bulk=sRT/HS;
  }else if(dcol.length){bulk=wmean;swe_mm=sumRT/100;}

    document.getElementById('mc-den').textContent = bulk!=null ? Math.round(bulk)+' kg/m³' : '—';
  document.getElementById('mc-swe').textContent = swe_mm!=null ? Math.round(swe_mm)+' mm' : '—';
  // Coverage / honesty line: say explicitly that DENSITY (not depth) was filled.
  if(swe_mm!=null && HS){
    if(filledCm>=0.5){
      document.getElementById('mc-cov-lbl').textContent='est · gap-filled'+(fromLayers?' · layer ρ':'');
      document.getElementById('mc-cov').textContent=Math.round(filledCm)+' cm';
    } else {
      document.getElementById('mc-cov-lbl').textContent='measured'+(fromLayers?' · layer ρ':'');
      document.getElementById('mc-cov').textContent='full depth';
    }
  } else {
    document.getElementById('mc-cov-lbl').textContent='';
    document.getElementById('mc-cov').textContent='';
  }
  const temps=(p.temperature||[]).map(t=>t.temp).filter(t=>t!=null);
  document.getElementById('mc-tmin').textContent=
    temps.length?Math.min(...temps).toFixed(1)+' °C':'—';
  // The mini column is drawn on the sheet, so it takes the sheet's ink and
  // follows the theme along with everything else below the command bar.
  const css=getComputedStyle(document.documentElement);
  const ink3=css.getPropertyValue('--ink3').trim()||'#56697d';
  const rule2=css.getPropertyValue('--rule2').trim()||'#c3d1de';
  const Wm=84,Hm=252,cx=22,cw=40,pad=10,col=Hm-2*pad;
  if(!HS||!strat.length){
    el.innerHTML=`<svg width="${Wm}" height="${Hm}" viewBox="0 0 ${Wm} ${Hm}" xmlns="http://www.w3.org/2000/svg">`+
      `<rect x="${cx}" y="${pad}" width="${cw}" height="${col}" fill="none" stroke="${rule2}" stroke-width="1" stroke-dasharray="3,3" rx="2"/>`+
      `<text x="${cx+cw/2}" y="${Hm/2}" text-anchor="middle" font-size="8" font-family="var(--mono)" fill="${ink3}">empty</text></svg>`;
    return;
  }
  const y=h=>pad+(1-(h/HS))*col;
  let s2=`<svg width="${Wm}" height="${Hm}" viewBox="0 0 ${Wm} ${Hm}" xmlns="http://www.w3.org/2000/svg" style="font-family:var(--mono)">`;
  s2+=`<text x="${cx+cw/2}" y="${pad-2}" text-anchor="middle" font-size="7" fill="${ink3}">${HS}</text>`;
  s2+=`<text x="${cx+cw/2}" y="${Hm-1}" text-anchor="middle" font-size="7" fill="${ink3}">0</text>`;
  strat.forEach(l=>{
    const yT=y(Math.min(l.top,HS)),yB=y(Math.max(l.bottom,0));
    const c=grainColor(l.gtype)||ink3;
    // hover tooltip: the mini can't label layers (esp. thin ones), so each
    // rect carries its facts — grain type, extent, hardness, wetness
    // the tooltip's ρ is THIS LAYER's recorded density (§7) — nothing else.
    // No layer density recorded = no ρ shown; interval densities from §5
    // stay in the SWE/bulk numbers where they belong.
    const tip=`${l.gtype||'?'} · ${l.top}-${l.bottom} cm${l.hardness?' · '+l.hardness:''}${l.wetness?' · '+l.wetness:''}${(l.layer_density!=null&&l.layer_density>0)?' · ρ '+Math.round(l.layer_density)+' kg/m³':''}`;
    s2+=`<rect x="${cx}" y="${yT}" width="${cw}" height="${Math.max(1,yB-yT)}" fill="${c}" fill-opacity="0.8" stroke="${rule2}" stroke-width="0.5"><title>${tip}</title></rect>`;
    if(yB-yT>10)s2+=`<text x="${cx+cw+4}" y="${(yT+yB)/2+2}" font-size="7" fill="${ink3}">${l.gtype||''}</text>`;
  });
  s2+=`<rect x="${cx}" y="${y(HS)}" width="${cw}" height="${y(0)-y(HS)}" fill="none" stroke="${rule2}" stroke-width="1" rx="2"/>`;
  s2+=`</svg>`;
  el.innerHTML=s2;
}

document.querySelectorAll('.toggles input').forEach(inp=>{
  inp.addEventListener('change',()=>{
    // Weather is multi-select, except that “None” remains exclusive. Selecting
    // None clears specific precipitation observations; selecting a specific
    // observation clears None. This preserves a coherent scientific value
    // while still allowing weather to change during the pit.
    const multi=inp.closest('[data-weather-multi]');
    if(multi&&inp.checked){
      const exclusive=multi.dataset.exclusiveValue||'';
      const peers=[...multi.querySelectorAll('input[type="checkbox"]')];
      if(exclusive){
        if(inp.value===exclusive)peers.forEach(r=>{if(r!==inp)r.checked=false;});
        else peers.forEach(r=>{if(r.value===exclusive)r.checked=false;});
      }
    }
    if(inp.name){document.querySelectorAll(`input[name="${inp.name}"]`).forEach(r=>r.closest('.tog').classList.toggle('on',r.checked));}
    else{inp.closest('.tog').classList.toggle('on',inp.checked);}
    tick();
  });
});

// Live redraw on ANY edit to a measurement table — including the last/only row.
// Event delegation on the <tbody> elements catches input/change from every
// current and future cell with one listener each.
['tb','db','lb','sb','ssab'].forEach(id=>{
  const body=document.getElementById(id);
  if(!body)return;
  // Validation runs IMMEDIATELY; only the profile redraw is debounced.
  // Both used to share the 300 ms timer, so a typo sat unmarked for a third of
  // a second and a correction stayed red just as long — long enough to read as
  // "it didn't fire". Recomputing the warning boxes is a handful of DOM reads;
  // rendering the mini profile is the expensive part worth throttling.
  // densityWarnings() paints BOTH #d-warn and #s-warn, despite the name
  const liveValidate=()=>{
    if(typeof densityWarnings==='function')densityWarnings();
    // a layer's camera unlocks the moment its interval is complete
    if(typeof refreshLayerCams==='function')refreshLayerCams();
    if(typeof syncChecklistFromEvidence==='function')syncChecklistFromEvidence();
  };
  body.addEventListener('input', ()=>{liveValidate();scheduleMini();});
  body.addEventListener('change', ()=>{liveValidate();scheduleMini();});   // <select> grain-type etc.
});


