function collect(){
  const loc=document.getElementById('loc').value;
  const location=loc==='__c'?document.getElementById('loc-c').value:loc;
  const veg=[];
  [{id:'vb',n:'bare'},{id:'vg',n:'grass'},{id:'vs',n:'shrub'},{id:'vd',n:'deadfall'}]
    .forEach(({id,n})=>{if(document.getElementById(id)?.checked)veg.push(n)});
  const zr=gv('utmz'),zm=zr.match(/^(\d{1,2})([A-Za-z])$/);
  /* Tables: num() everywhere, and rows whose cells are ALL empty are skipped —
     an abandoned "+ add" row no longer fabricates a 0 cm / 0.0 degC reading. */
  const temperature=[];
  document.querySelectorAll('#tb tr').forEach(tr=>{
    const ins=tr.querySelectorAll('input');
    const h=num(ins[0].value),t=num(ins[1].value);
    if(h===null&&t===null)return;
    temperature.push({height:h,temp:t});
  });
  const density=[];
  document.querySelectorAll('#db tr').forEach(tr=>{
    const ins=tr.querySelectorAll('input');
    const r={top:num(ins[0].value),bottom:num(ins[1].value),
      a:num(ins[2].value),b:num(ins[3].value),c:num(ins[4].value)};
    if(r.top===null&&r.bottom===null&&r.a===null&&r.b===null&&r.c===null)return;
    density.push(r);
  });
  const lwc=[];
  document.querySelectorAll('#lb tr').forEach(tr=>{
    const ins=tr.querySelectorAll('input');
    const r={top:num(ins[0].value),bottom:num(ins[1].value),
      a:num(ins[2].value),b:num(ins[3].value)};
    if(r.top===null&&r.bottom===null&&r.a===null&&r.b===null)return;
    lwc.push(r);
  });
  const stratigraphy=[];
  document.querySelectorAll('#sb tr').forEach(tr=>{
    const ins=tr.querySelectorAll('input');const sels=tr.querySelectorAll('select');
    const r={top:num(ins[0].value),bottom:num(ins[1].value),
      gmin:num(ins[2].value),gmax:num(ins[3].value),gavg:num(ins[4].value),
      gtype:sels[0]?.value||'',hardness:sels[1]?.value||'',wetness:sels[2]?.value||'',
      // ρA / ρB / mean. layer_density stays the canonical single value the
      // exporter and the profile already understand — it is the mean when both
      // readings are present, or whichever one was given.
      layer_density_a:_layerDensityOn?num(ins[5]?.value):null,
      layer_density_b:_layerDensityOn?num(ins[6]?.value):null,
      layer_density:_layerDensityOn?(num(ins[7]?.value)??num(ins[5]?.value)??num(ins[6]?.value)):null,
      comments:ins[_layerDensityOn?8:5]?.value||''};
    if(r.top===null&&r.bottom===null&&r.gmin===null&&r.gmax===null&&r.gavg===null&&r.layer_density===null&&r.layer_density_a===null&&r.layer_density_b===null&&!r.comments)return;
    stratigraphy.push(r);
  });
  const ssa=[];
  document.querySelectorAll('#ssab tr').forEach(tr=>{
    const ins=tr.querySelectorAll('input');const sels=tr.querySelectorAll('select');
    const r={height:num(ins[0].value),signal:num(ins[1].value),
      reflectance:num(ins[2].value),ssa:num(ins[3].value),
      grain_type:sels[0]?.value||'',comments:ins[4]?.value||''};
    if(r.height===null&&r.signal===null&&r.reflectance===null&&r.ssa===null&&!r.comments)return;
    ssa.push(r);
  });
  const specStr=gv('ssa-spec'),calvStr=gv('ssa-calv');
  // Collect the instrument checklist without inventing answers.
  // Y and N are both explicit choices; neither button lit means unanswered and
  // is sent as null. Serial is meaningful only for Y (the field is disabled in
  // the other two states). The DOM index tracks buildInst's non-group rows;
  // survey/documentation rows simply have no serial input.
  const instruments=[];
  let di=0;
  INST.forEach((it)=>{
    if(it.g)return;
    const i=di++;
    const name=it.w?(document.getElementById('on'+i)?.value||'').trim():it.n;
    if(it.w&&!name)return;   // empty write-in row -> nothing to record
    const yOn=!!document.getElementById('yy'+i)?.classList.contains('on');
    const nOn=!!document.getElementById('yn'+i)?.classList.contains('on');
    const used=yOn?'Y':(nOn?'N':null);
    const sn=(used==='Y' ? (document.getElementById('sn'+i)?.value||'') : '').trim();
    instruments.push({name, sn, used});
  });
  // Density cutter — multi-select (100/250/1000 cc), joined as a string
  const cutters=[];
  [['dc100','100'],['dc250','250'],['dc1000','1000']].forEach(([id,v])=>{
    if(document.getElementById(id)?.checked)cutters.push(v);
  });
  const density_cutter=cutters.length?cutters.join(', ')+' cc':'';
  const ssaOp=gv('ssa-operator').trim();
  return{
    site_id:_loaded_site_id,
    meta:{pit_id:document.getElementById('pitid').textContent.trim(),
      // null  -> auto title (pit · location · date)
      // ''    -> no title at all; the renderer must tell these apart, so the
      //          empty string is meaningful here and is NOT collapsed to null
      no_instruments:!!document.getElementById('none-inst')?.checked,
      no_tasks:!!document.getElementById('none-task')?.checked,
      figure_title:document.getElementById('fig-notitle')?.checked
        ? '' : (gv('fig-title').trim()||null),
      location,site:gv('site'),campaign:gv('campaign'),
      total_depth:num(gv('depth')),
      utm_easting:num(gv('utme')),utm_northing:num(gv('utmn')),
      utm_zone_number:zm?parseInt(zm[1]):null,utm_zone_letter:zm?zm[2]:'',
      latitude:num(gv('lat')),longitude:num(gv('lon')),
      coord_source:gv('utme')?'utm':'latlon',
      elevation:num(gv('elev')),slope_angle:num(gv('slope')),
      recorded_by:gv('recby'),surveyors:gv('surv'),date:gv('date'),
      pit_open_time:gv('po'),temp_time_start:gv('ts'),temp_time_end:gv('te'),
      gps_device:gv('gps'),
      gps_uncertainty:num(gv('gps-unc')),
      gps_uncertainty_unit:gv('gps-unc-unit'),
      lwc_device:gv('lwc-dev'),lwc_device_sn:gv('lwc-sn'),
      density_cutter:density_cutter,
      comments:gv('comments'),flags:gv('flags')||'None',
      comment_weather:gv('cmt-weather'),comment_pit:gv('cmt-pit'),
      comment_hardness:gv('cmt-hardness'),comment_misc:gv('cmt-misc')},
    weather:{precip_rate:gcs('pr'),precip_type:gcs('pt'),sky:gcs('sky'),wind:gcs('wind')},
    ground:{condition:gcs('gc'),roughness:gr('gr'),canopy:gr('tc'),
      snow_cover:gr('scc'),standing_water:gr('sw'),
      vegetation:veg,veg_height:num(gv('vh')),
      melt_evidence:gr('melt'),
      swe_samples:['a','b','c'].map(k=>({sample:k.toUpperCase(),
        depth:num(gv('ib-d-'+k)),swe:num(gv('ib-s-'+k)),density:num(gv('ib-r-'+k))}))
        .filter(r=>r.depth!==null||r.swe!==null||r.density!==null)},
    temperature,density,lwc,stratigraphy,ssa,
    instruments,
    ssa_calibration:{
      instrument:gv('ssa-inst'),
      operator:ssaOp,
      spectralon:specStr?specStr.split(',').map(s=>parseFloat(s.trim())).filter(x=>!isNaN(x)):[],
      calib_values:calvStr?calvStr.split(',').map(s=>parseFloat(s.trim())).filter(x=>!isNaN(x)):[],
      measured_at:gv('ssa-cal-time'),notes:gv('ssa-notes')}
  };
}

// populate(): exact inverse of collect(). Used by pit loading AND draft
// restore, so both features share one battle-tested path. -------------------
function sv(id,v){const el=document.getElementById(id);if(el)el.value=(v===null||v===undefined)?'':v;}
function setRadio(name,val){
  document.querySelectorAll(`input[name="${name}"]`).forEach(r=>r.checked=(r.value===val));
}
function weatherValues(val){
  if(Array.isArray(val))return val.map(v=>String(v));
  if(val===null||val===undefined||val==='')return [];
  // Older pits stored one scalar string. Accept it unchanged. Semicolon-joined
  // text is also tolerated for normalized/reporting round trips.
  return String(val).split(/\s*;\s*/).filter(Boolean);
}
function setChecks(name,val){
  const wanted=new Set(weatherValues(val));
  document.querySelectorAll(`input[name="${name}"]`).forEach(r=>r.checked=wanted.has(r.value));
}
function refreshTogs(){
  document.querySelectorAll('.toggles input').forEach(inp=>{
    inp.closest('.tog').classList.toggle('on',inp.checked);
  });
  if(typeof refreshClearableRadioGroups==='function')refreshClearableRadioGroups();
}
function clearTables(){['tb','db','lb','sb','ssab'].forEach(id=>document.getElementById(id).innerHTML='');}

function populate(p){
  if(!p||!p.meta)return;
  _restoring=true;
  try{
    const m=p.meta||{},wx=p.weather||{},g=p.ground||{};
    const locSel=document.getElementById('loc');
    const opts=[...locSel.options].map(o=>o.value||o.textContent);
    if(m.location&&opts.includes(m.location)){
      locSel.value=m.location;
      document.getElementById('loc-c').style.display='none';
      document.getElementById('loc-c').value='';
    }else if(m.location){
      locSel.value='__c';
      document.getElementById('loc-c').style.display='block';
      document.getElementById('loc-c').value=m.location;
    }else{
      locSel.value='';
      document.getElementById('loc-c').style.display='none';
    }
    sv('site',m.site);sv('date',m.date);sv('campaign',m.campaign||'__CAMPAIGN__');
    if(m.pit_id&&m.pit_id!=='—')_pe=true;   // keep the stored ID, don't regenerate
    document.getElementById('pitid').textContent=m.pit_id||'—';
    document.getElementById('tb-pid').textContent=m.pit_id||'—';
    sv('depth',m.total_depth);sv('po',m.pit_open_time);sv('slope',m.slope_angle);
    sv('recby',m.recorded_by);sv('surv',m.surveyors);
    sv('gps',m.gps_device);sv('gps-unc',m.gps_uncertainty);
    sv('gps-unc-unit',m.gps_uncertainty_unit||'m');
    _cl=true;   // suppress the converters while restoring both coordinate sets
    sv('utme',m.utm_easting);sv('utmn',m.utm_northing);
    sv('utmz',(m.utm_zone_number?String(m.utm_zone_number):'')+(m.utm_zone_letter||''));
    sv('elev',m.elevation);sv('lat',m.latitude);sv('lon',m.longitude);
    setTimeout(()=>_cl=false,250);
    sv('flags',m.flags);sv('comments',m.comments);
    sv('cmt-weather',m.comment_weather);sv('cmt-pit',m.comment_pit);
    sv('cmt-hardness',m.comment_hardness);sv('cmt-misc',m.comment_misc);
    sv('ts',m.temp_time_start);sv('te',m.temp_time_end);
    sv('lwc-dev',m.lwc_device);sv('lwc-sn',m.lwc_device_sn);
    const noTitle=m.figure_title==='';
    const cb=document.getElementById('fig-notitle');
    if(cb)cb.checked=noTitle;
    sv('fig-title',noTitle?'':m.figure_title);
    if(typeof toggleFigTitle==='function')toggleFigTitle();
    const dc=m.density_cutter||'';
    document.getElementById('dc100').checked=/\b100\b/.test(dc);
    document.getElementById('dc250').checked=/\b250\b/.test(dc);
    document.getElementById('dc1000').checked=/\b1000\b/.test(dc);
    setChecks('pr',wx.precip_rate);setChecks('pt',wx.precip_type);
    setChecks('sky',wx.sky);setChecks('wind',wx.wind);
    setChecks('gc',g.condition);setRadio('gr',g.roughness);
    setRadio('tc',g.canopy);setRadio('scc',g.snow_cover);setRadio('sw',g.standing_water);
    const veg=g.vegetation||[];
    document.getElementById('vb').checked=veg.includes('bare');
    document.getElementById('vg').checked=veg.includes('grass');
    document.getElementById('vs').checked=veg.includes('shrub');
    document.getElementById('vd').checked=veg.includes('deadfall');
    sv('vh',g.veg_height);sv('nd',g.new_depth);sv('ns',g.new_swe);
    setRadio('melt',g.melt_evidence);
    (g.swe_samples||[]).forEach(r=>{
      const k=(r.sample||'').toLowerCase();
      if(!'abc'.includes(k)||!k)return;
      sv('ib-d-'+k,r.depth);sv('ib-s-'+k,r.swe);sv('ib-r-'+k,r.density);
    });
    clearTables();
    (p.temperature||[]).forEach(r=>{
      const tr=addRow('t',false);const ins=tr.querySelectorAll('input');
      ins[0].value=(r.height===null||r.height===undefined)?'':r.height;
      ins[1].value=(r.temp===null||r.temp===undefined)?'':r.temp;
    });
    (p.density||[]).forEach(r=>{
      const tr=addRow('d',false);const ins=tr.querySelectorAll('input');
      [['top',0],['bottom',1],['a',2],['b',3],['c',4]].forEach(([k,i])=>{
        ins[i].value=(r[k]===null||r[k]===undefined)?'':r[k];});
      calcAvg(tr);
    });
    (p.lwc||[]).forEach(r=>{
      const tr=addRow('l',false);const ins=tr.querySelectorAll('input');
      [['top',0],['bottom',1],['a',2],['b',3]].forEach(([k,i])=>{
        ins[i].value=(r[k]===null||r[k]===undefined)?'':r[k];});
    });
    // per-layer density column auto-appears when the loaded pit carries values
    setLayerDensity((p.stratigraphy||[]).some(r=>r&&(r.layer_density!=null||r.layer_density_a!=null||r.layer_density_b!=null)));
    (p.stratigraphy||[]).forEach(r=>{
      const tr=addRow('s',false);
      const ins=tr.querySelectorAll('input');const sels=tr.querySelectorAll('select');
      [['top',0],['bottom',1],['gmin',2],['gmax',3],['gavg',4]].forEach(([k,i])=>{
        ins[i].value=(r[k]===null||r[k]===undefined)?'':r[k];});
      if(r.gtype)sels[0].value=r.gtype;
      if(r.hardness)sels[1].value=r.hardness;
      if(r.wetness)sels[2].value=r.wetness;
    if(_layerDensityOn&&ins[5]){
        // a pit saved before ρA/ρB existed carries only layer_density; put it
        // in A so nothing is lost and the mean still reads correctly
        const a=(r.layer_density_a??(r.layer_density_b==null?r.layer_density:null));
        ins[5].value=(a===null||a===undefined)?'':a;
        if(ins[6])ins[6].value=(r.layer_density_b===null||r.layer_density_b===undefined)?'':r.layer_density_b;
        if(typeof calcLayerAvg==='function')calcLayerAvg(ins[5]);
      }
      ins[_layerDensityOn?8:5].value=r.comments||'';
    });
    (p.ssa||[]).forEach(r=>{
      const tr=addRow('sa',false);
      const ins=tr.querySelectorAll('input');const sels=tr.querySelectorAll('select');
      [['height',0],['signal',1],['reflectance',2],['ssa',3]].forEach(([k,i])=>{
        ins[i].value=(r[k]===null||r[k]===undefined)?'':r[k];});
      if(r.grain_type)sels[0].value=r.grain_type;
      ins[4].value=r.comments||'';
    });
    // Instrument restore is BY NAME, never by array position. collect() omits
    // the blank write-in ("Other") row entirely, so a saved payload can be
    // shorter than the checklist — restoring positionally shifted every row
    // after the write-in by one (a pit saved with "Pit pictures = Y" came back
    // as "Stratigraphy pictures = Y", and the photo inputs those rows gate
    // stayed locked). Build a name -> DOM-index map from INST itself and match
    // against it; the single write-in row absorbs whatever unrecognized name
    // the payload carries.
    ['inst','task'].forEach(g=>{
      const cb=document.getElementById('none-'+g);
      if(cb){cb.checked=!!(g==='inst'?p.meta?.no_instruments:p.meta?.no_tasks);}
    });
    const _idxByName={}; let _wIdx=null, _di=0;
    INST.forEach(it=>{
      if(it.g)return;
      const i=_di++;
      if(it.w)_wIdx=i; else _idxByName[it.n]=i;
    });
    // Clear the board first. An omitted row is missing/unanswered, not an
    // implicit N; this also prevents stale answers from the previously loaded pit.
    for(let i=0;i<_di;i++){
      const on=document.getElementById('on'+i); if(on)on.value='';
      if(document.getElementById('yy'+i))setyn(i,null);
    }
    (p.instruments||[]).forEach(it=>{
      const name=(it.name||'').trim();
      let i=_idxByName[name];
      if(i===undefined){
        if(_wIdx===null||!name)return;      // unknown name, no write-in row to hold it
        i=_wIdx;
        const on=document.getElementById('on'+i); if(on)on.value=name;
      }
      const used=it.used==='Y'?'Y':(it.used==='N'?'N':null);
      if(document.getElementById('yy'+i))setyn(i,used);
      const snEl=document.getElementById('sn'+i);
      if(snEl)snEl.value=(used==='Y'&&it.sn&&it.sn!=='—')?it.sn:'';
    });
    // applied AFTER the rows so the lock lands on restored values
    if(typeof onNoneGroup==='function'){onNoneGroup('inst');onNoneGroup('task');}
    const sc=p.ssa_calibration||{};
    sv('ssa-inst',sc.instrument);sv('ssa-cal-time',sc.measured_at);
    sv('ssa-operator',sc.operator);
    sv('ssa-spec',(sc.spectralon||[]).join(','));
    sv('ssa-calv',(sc.calib_values||[]).join(','));
    sv('ssa-notes',sc.notes);
    refreshTogs();
    ['t','d','l','s','sa'].forEach(cnt);
    if(typeof refreshAttachUI==='function')refreshAttachUI();
  }finally{
    _restoring=false;
  }
  tick();
}

