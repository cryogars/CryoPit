// "No title" hides the title box entirely rather than leaving an input that
// does nothing — a disabled field still invites you to type in it. Unchecking
// brings the box back with whatever was in it.
function toggleFigTitle(){
  const off=document.getElementById('fig-notitle')?.checked;
  const row=document.getElementById('fig-title-row');
  if(row)row.style.display=off?'none':'';
  // Re-render so the change is visible immediately — but ONLY if a profile is
  // already drawn. Rendering on a blank §10 would fire a server round-trip the
  // user never asked for, and fail noisily on a pit with no layers yet.
  const wrap=document.getElementById('profile-wrap');
  if(wrap&&wrap.querySelector('img')&&typeof drawProfile==='function')drawProfile();
}


const PROFILE_EMPTY_MESSAGE='No profile data yet. Add temperature, density, or stratigraphy observations to generate a snow profile.';
const PROFILE_NO_STRAT_MESSAGE='No complete stratigraphy layers yet. Profile generated without stratigraphy.';

function profileDataState(p){
  const finite=v=>typeof v==='number'&&Number.isFinite(v);
  const hasTemp=(p.temperature||[]).some(r=>finite(r.height)&&finite(r.temp));
  const hasDensity=(p.density||[]).some(r=>
    finite(r.top)&&finite(r.bottom)&&[r.a,r.b,r.c].some(v=>finite(v)&&v>0));
  const hasStrat=(p.stratigraphy||[]).some(r=>finite(r.top)&&finite(r.bottom));
  return{hasAny:hasTemp||hasDensity||hasStrat,hasStrat};
}

// §11 profile: rendered SERVER-SIDE (cryopit/plot.py — the reference
// figure with ICSSG symbols and the documented gap-fill styling). This just
// posts the current form state and shows the returned PNG.
function drawProfile(){
  const wrap=document.getElementById('profile-wrap');
  const state=profileDataState(collect());
  if(!state.hasAny){
    wrap.classList.remove('is-rendering');
    wrap.innerHTML='<p class="pf-msg">'+PROFILE_EMPTY_MESSAGE+'</p>';
    const pip=document.getElementById('p10');
    if(pip)pip.classList.remove('done');
    const pm=document.getElementById('p10-meta');
    if(pm)pm.textContent='waiting for data';
    return;
  }
  // Keep the existing figure on screen while the new one renders. It used to be
  // destroyed before the request even started, so every re-render blanked the
  // panel for the ~2 s the round trip takes — read as a "blink" when the only
  // thing that actually changed was one number. The figure is drawn by
  // matplotlib on the server (publication typography, hatching, ICSSG grain
  // symbols), so the delay is inherent; the blank was not.
  const prev=wrap.querySelector('img');
  if(prev){wrap.classList.add('is-rendering');}
  else{wrap.innerHTML='<p class="pf-msg">rendering…</p>';}
  const{p,e}=validate();
  if(e.length){wrap.classList.remove('is-rendering');
    wrap.innerHTML='<p class="pf-msg err">Render blocked: '+esc(e.join('; '))+'</p>';return;}
  post('/api/profile',p)
    .then(async r=>{
      if(!r.ok){const j=await r.json().catch(()=>({msg:'render failed'}));throw new Error(j.msg||'render failed');}
      return r.blob();
    })
    .then(b=>{
      const url=URL.createObjectURL(b);
      wrap.classList.remove('is-rendering');
      wrap.innerHTML='';
      if(!state.hasStrat){
        const note=document.createElement('p');
        note.className='pf-note';
        note.textContent=PROFILE_NO_STRAT_MESSAGE;
        wrap.appendChild(note);
      }
      const img=document.createElement('img');
      img.src=url;img.alt='Snow profile';img.style.maxWidth='100%';
      img.onload=()=>URL.revokeObjectURL(url);
      wrap.appendChild(img);
      const pip=document.getElementById('p10');
      if(pip)pip.classList.add('done');
      const pm=document.getElementById('p10-meta');
      if(pm)pm.textContent=state.hasStrat?'rendered':'rendered · no stratigraphy';
    })
    .catch(err=>{wrap.classList.remove('is-rendering');
      wrap.innerHTML='<p class="pf-msg err">'+esc(err.message)+'</p>';});
}

